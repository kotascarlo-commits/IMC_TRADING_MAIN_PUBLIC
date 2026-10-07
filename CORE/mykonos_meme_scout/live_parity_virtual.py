"""Virtual lifecycle using the existing local bank and frozen entry/exit rules.

Prepared Jupiter packets are inputs, never permissions to sign or broadcast.
This experimental transport can close TECHNICAL virtual cycles only. Mainnet
parity, persistence/recovery and independent forward setups remain unaccepted.
"""
import hashlib
import math
import time
from decimal import Decimal, InvalidOperation
from solders.pubkey import Pubkey

from . import paper_forward as paper
from .cabal_security import authority_off
from .jupiter_instructions import validate_auxiliary
from .jupiter_prepare import assemble
from .jupiter_simulation import associated, SOL, TOKEN
from .jupiter_stateful import StatefulJupiterBank


class StatefulVirtualCycle:
    def __init__(self, bank, mint, *, clock=time.time):
        authority_off()
        if type(bank) is not StatefulJupiterBank:
            raise ValueError('native_local_bank_required')
        self.bank, self.mint, self.clock = bank, mint, clock
        self.cfg = paper.load_config()
        self.position = None
        self.closed = []
        self.stopped = False

    def meta(self, key):
        return None  # No new discretionary/forced exit is introduced.

    def _balance(self):
        account = self.bank.account(associated(self.bank.wallet, self.mint, TOKEN))
        if account is None: return 0
        if (str(account.owner) != TOKEN or len(account.data) != 165
                or account.data[:32] != bytes(Pubkey.from_string(self.mint))
                or account.data[32:64] != bytes(Pubkey.from_string(self.bank.wallet))
                or account.data[108] != 1):
            raise ValueError('virtual_token_program_or_schema_unsupported')
        return int.from_bytes(account.data[64:72], 'little')

    def _fresh_market(self, snapshot):
        if (snapshot.get('address') != self.mint or snapshot.get('market_data_stale') is not False
                or not 0 <= self.clock()-paper.epoch(snapshot['captured_at']) <= self.cfg['snapshot_max_age_seconds']):
            raise ValueError('virtual_market_unknown_or_stale')

    def _guard(self, packet, side):
        try:
            cfg = authority_off()
            if self.stopped: raise ValueError('virtual_cycle_stopped')
            q = packet['query']
            at = packet['quoted_at']
            impact = Decimal(str(packet['build'].get('priceImpactPct')))
            if (type(at) not in (int, float) or not math.isfinite(at)
                    or not 0 <= self.clock()-at <= cfg['quote_max_age_seconds']
                    or packet.get('source') != 'https://api.jup.ag/swap/v2/build'
                    or q['taker'] != self.bank.wallet
                    or q['inputMint'] != (SOL if side == 'BUY' else self.mint)
                    or q['outputMint'] != (self.mint if side == 'BUY' else SOL)
                    or not 0 < int(q['amount']) < 2**64
                    or type(q['slippageBps']) is not int
                    or not 0 <= q['slippageBps'] <= cfg['max_slippage_bps']
                    or not impact.is_finite() or abs(impact)*10000 > cfg['max_price_impact_bps']):
                raise ValueError('virtual_quote_binding_unknown_or_stale')
            if self.position:
                if self.bank.state_hash() != self.position['state_hash']:
                    raise ValueError('virtual_hold_state_conflict')
                if int(q['amount']) != self.position['token_raw'] or self._balance() != self.position['token_raw']:
                    raise ValueError('virtual_sell_not_owned_by_buy')
            elif side == 'BUY':
                cash = self.bank.account(self.bank.wallet).lamports
                cap = min(int(Decimal(cash)*Decimal(cfg['position_fraction'])), cash-int(cfg['reserve_lamports']))
                if int(q['amount']) > cap:
                    raise ValueError('virtual_position_size_risk_limit')
            return cfg
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            self.stopped = True
            raise ValueError('virtual_guard_stop:' + str(error)) from error

    def _prepare(self, packet, side):
        self._guard(packet, side)
        validate_auxiliary(packet['build'], packet['query'])
        expected = assemble(packet['build'], packet['query'], packet['tables'], packet['blockhash'])
        if bytes(expected) != bytes(packet['transaction']):
            self.stopped = True
            raise ValueError('virtual_prepared_transaction_bytes_mismatch')
        return expected

    def _execute(self, packet, side, *, admission_check):
        def before_commit():
            self._guard(packet, side)
            admission_check()
        tx = self._prepare(packet, side)
        preview = self.bank.preview(tx, packet['tables'], packet['query'], packet['build'], packet['blockhash'])
        cfg = self._guard(packet, side)
        if not 0 < int(preview['cost']['network_fee_lamports']) <= int(cfg['max_order_fee_lamports']):
            self.stopped = True
            raise ValueError('virtual_fee_risk_limit')
        try:
            before_commit()
            receipt = self.bank.execute(tx, packet['tables'], packet['query'], packet['build'], packet['blockhash'],
                precommit_check=before_commit)
        except Exception:
            self.stopped = True
            raise
        return receipt

    def buy(self, packet, snapshot, previous, cabal, owner, holders):
        if self.position is not None or self.closed or self.stopped:
            raise ValueError('virtual_position_duplicate_or_not_independent')
        entry = {}
        def admission():
            self._fresh_market(snapshot)
            reasons = paper.brain(snapshot, previous, self.cfg, self.clock(), evidence=entry,
                cabal_observation=cabal, cabal_owner=owner, cabal_holder_observation=holders)
            if reasons or entry.get('security_complete') is not True or entry.get('holder_complete') is not True:
                raise ValueError('virtual_entry_rejected:' + ','.join(reasons))
        admission()
        if self._balance() != 0:
            raise ValueError('virtual_initial_token_inventory_not_empty')
        cash_before = self.bank.account(self.bank.wallet).lamports
        receipt = self._execute(packet, 'BUY', admission_check=admission)
        acquired = self._balance()
        cost = receipt['cost']
        if acquired <= 0 or acquired != int(cost['output_raw']):
            self.stopped = True
            raise ValueError('virtual_buy_inventory_reconciliation_failed')
        self.position = {'input_lamports': int(packet['query']['amount']),
            'entry_cost_lamports': int(cost['total_fee_and_net_account_lamports']),
            'opened_at': self.clock(), 'token_raw': acquired, 'best_mark': 0,
            'state_hash': self.bank.state_hash(), 'cash_before': cash_before,
            'buy': receipt, 'entry': entry, 'exit_reason': None}
        return receipt

    def hold(self):
        authority_off()
        if (not self.position or self.stopped or self.bank.poisoned
                or self.bank.state_hash() != self.position['state_hash']
                or self._balance() != self.position['token_raw']):
            self.stopped = True
            raise ValueError('virtual_hold_not_preserved')
        return {'token_raw': self._balance(), 'state_hash': self.bank.state_hash()}

    def mark(self, packet, snapshot):
        self.hold(); self._fresh_market(snapshot)
        tx = self._prepare(packet, 'SELL')
        preview = self.bank.preview(tx, packet['tables'], packet['query'], packet['build'], packet['blockhash'])
        self.hold()
        value = int(preview['cost']['wallet_cash_delta_lamports'])
        self.position['best_mark'] = max(value, self.position['best_mark'])
        self.position['exit_reason'] = paper.PaperBook.exit_reason(self, self.position, value)
        return self.position['exit_reason']

    def sell(self, packet, snapshot):
        self.hold(); self._fresh_market(snapshot)
        # Re-evaluate the real existing exit on THIS fresh packet, not an old mark.
        if self.mark(packet, snapshot) is None:
            raise ValueError('virtual_exit_not_triggered')
        receipt = self._execute(packet, 'SELL', admission_check=lambda: self._fresh_market(snapshot))
        delta = int(self.position['buy']['cost']['wallet_cash_delta_lamports']) + int(receipt['cost']['wallet_cash_delta_lamports'])
        cash_after = self.bank.account(self.bank.wallet).lamports
        if self._balance() != 0 or cash_after-self.position['cash_before'] != delta:
            self.stopped = True
            raise ValueError('virtual_close_reconciliation_failed')
        stress = ((self.position['input_lamports'] + int(receipt['cost']['output_raw'])) *
                  self.cfg['extra_fee_bps_per_side'] + 9999)//10000
        result = {'status': 'CLOSED_TECHNICAL_VIRTUAL', 'LIVE_PARITY_CLOSED': False,
            'reconciliation': 'PASS', 'token_remaining_raw': 0,
            'cash_before_lamports': self.position['cash_before'], 'cash_after_lamports': cash_after,
            'cash_delta_lamports': delta, 'existing_stress_cost_lamports': stress,
            'modeled_net_lamports': delta-stress,
            'network_fees_lamports': int(self.position['buy']['cost']['network_fee_lamports']) + int(receipt['cost']['network_fee_lamports']),
            'buy': self.position['buy'], 'sell': receipt, 'entry': self.position['entry'],
            'exit_reason': self.position['exit_reason'],
            'evidence_domain': 'EXPERIMENTAL_LOCAL_VM_NOT_ACCEPTED_MAINNET_PARITY',
            'signing': False, 'broadcast': False, 'private_key_access': False, 'real_money_moved': False}
        self.closed.append(result); self.position = None
        return result
