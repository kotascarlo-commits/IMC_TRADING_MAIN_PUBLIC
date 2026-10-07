"""Experimental local SVM transport; no keys, signing or network sender.

Initial accounts/programs must come from an attributable Mainnet snapshot.
Only local VM execution can change token accounts after initialization. This
module proves state continuity, not signals, security or Mainnet feature parity.
"""
import base64
import hashlib
import json
import time

from solders.account import Account
from solders.clock import Clock
from solders.litesvm import LiteSVM
from solders.pubkey import Pubkey
from solders.rent import Rent
from solders.transaction_metadata import FailedTransactionMetadata

from .jupiter_simulation import TOKEN, message_keys, account_simulation

SYSTEM = '11111111111111111111111111111111'
UPGRADEABLE = 'BPFLoaderUpgradeab1e11111111111111111111111'
LEGACY_LOADER = 'BPFLoader2111111111111111111111111111111111'
CLOCK = 'SysvarC1ock11111111111111111111111111111111'
RENT = 'SysvarRent111111111111111111111111111111111'
DOMAIN = 'EXPERIMENTAL_STATEFUL_MAINNET_SNAPSHOT_NOT_CHAIN'
TOKEN2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'


def imported_account(value):
    data = value.get('data') if isinstance(value, dict) else None
    if (not isinstance(data, list) or len(data) != 2 or data[1] != 'base64'
            or not isinstance(data[0], str)):
        raise ValueError('mainnet_account_encoding_unknown')
    if (type(value.get('lamports')) is not int or not 0 <= value['lamports'] < 2**64
            or type(value.get('executable')) is not bool
            or type(value.get('rentEpoch', 0)) is not int or not 0 <= value.get('rentEpoch', 0) < 2**64
            or not isinstance(value.get('owner'), str)):
        raise ValueError('mainnet_account_schema_unknown')
    return Account(value['lamports'], base64.b64decode(data[0], validate=True),
                   Pubkey.from_string(value['owner']), value['executable'], value.get('rentEpoch', 0))


def fingerprint(account):
    # LiteSVM preview retains the empty closed WSOL account; commit removes it.
    # Both represent absence. Never normalize a funded or data-bearing account.
    if (account is not None and account.lamports == 0 and not account.data
            and not account.executable and str(account.owner) == SYSTEM):
        return None
    return hashlib.sha256(bytes(account)).hexdigest() if account is not None else None


def token_state(account, index):
    if account is None or str(account.owner) != TOKEN or len(account.data) != 165:
        return None
    data = account.data
    return {'accountIndex': index, 'mint': str(Pubkey.from_bytes(data[:32])),
            'owner': str(Pubkey.from_bytes(data[32:64])), 'programId': TOKEN,
            'uiTokenAmount': {'amount': str(int.from_bytes(data[64:72], 'little')), 'decimals': None}}


class StatefulJupiterBank:
    def __init__(self, wallet, accounts, programs, slot, *, virtual_cash_lamports=None):
        if type(slot) is not int or slot <= 0:
            raise ValueError('mainnet_snapshot_slot_unknown')
        self.wallet = str(Pubkey.from_string(wallet))
        self.anchor_slot = slot
        self._svm = (LiteSVM().with_mainnet_features().with_sigverify(False)
                     .with_blockhash_check(False).with_transaction_history(0))
        self._accounts = {}
        self._committed = set()
        self._dirty = set()
        self.poisoned = False
        self.program_hashes = {}
        for address, program in programs.items():
            if program['loader'] not in (UPGRADEABLE, LEGACY_LOADER) or not program['bytes'].startswith(b'\x7fELF'):
                raise ValueError('mainnet_program_loader_unknown')
            self._svm.add_program_with_loader(Pubkey.from_string(address), program['bytes'], Pubkey.from_string(program['loader']))
            self.program_hashes[address] = hashlib.sha256(program['bytes']).hexdigest()
        for address, value in accounts.items():
            if value is None:
                self._accounts[address] = None
                continue
            account = imported_account(value)
            if address == CLOCK:
                self._svm.set_clock(Clock.from_bytes(account.data))
            elif address == RENT:
                self._svm.set_rent(Rent.from_bytes(account.data))
            else:
                self._svm.set_account(Pubkey.from_string(address), account)
            self._accounts[address] = account
        payer = self._svm.get_account(Pubkey.from_string(self.wallet))
        if payer is None or str(payer.owner) != SYSTEM or payer.data or payer.executable:
            raise ValueError('mainnet_public_wallet_unknown')
        self.initial_real_cash = payer.lamports
        if virtual_cash_lamports is not None:
            if type(virtual_cash_lamports) is not int or virtual_cash_lamports <= 0:
                raise ValueError('virtual_initial_cash_invalid')
            # Explicit initial virtual SOL only. No token account is credited.
            payer = Account(virtual_cash_lamports, payer.data, payer.owner, False, payer.rent_epoch)
            self._svm.set_account(Pubkey.from_string(self.wallet), payer)
        self.initial_virtual_cash = payer.lamports
        self.created_at = time.time()

    def account(self, address):
        return self._svm.get_account(Pubkey.from_string(address))

    def import_additional(self, accounts):
        """Only previously absent keys; never overwrite the fork or BUY assets."""
        accepted = {}
        for address, value in accounts.items():
            if address in self._accounts or address in self._dirty or self.account(address) is not None:
                if value is not None and fingerprint(imported_account(value)) != fingerprint(self.account(address)):
                    raise ValueError('hold_mainnet_state_conflict:' + address)
                continue
            if value is None:
                accepted[address] = None
                continue
            account = imported_account(value)
            if str(account.owner) == TOKEN2022:
                raise ValueError('hold_token_program_unsupported')
            if str(account.owner) == TOKEN and len(account.data) not in (82, 165):
                raise ValueError('hold_token_schema_unsupported')
            # A newly observed wallet token cannot be injected after a BUY.
            state = token_state(account, 0)
            if self._committed and state and state['owner'] == self.wallet:
                raise ValueError('hold_wallet_token_injection_blocked')
            accepted[address] = account
        for address, account in accepted.items():
            if account is not None:
                self._svm.set_account(Pubkey.from_string(address), account)
            self._accounts[address] = account

    def preview(self, tx, tables, query, build, expected_blockhash):
        if self.poisoned or query['taker'] != self.wallet or str(tx.message.recent_blockhash) != expected_blockhash:
            raise ValueError('stateful_transaction_binding_unknown')
        keys, loaded = message_keys(tx, tables)
        before = [self.account(key) for key in keys]
        result = self._svm.simulate_transaction(tx)
        if isinstance(result, FailedTransactionMetadata):
            raise ValueError('stateful_simulation_failed:' + str(result.err()))
        post = dict((str(key), account) for key, account in result.post_accounts())
        after = [post.get(key, before[i]) for i, key in enumerate(keys)]
        if any(fingerprint(self.account(key)) != fingerprint(before[i]) for i, key in enumerate(keys)):
            self.poisoned = True
            raise ValueError('preview_mutated_bank')
        pre_tokens, post_tokens = [], []
        for values, output in ((before, pre_tokens), (after, post_tokens)):
            for index, account in enumerate(values):
                state = token_state(account, index)
                if state is not None:
                    mint = self.account(state['mint'])
                    if mint is None or str(mint.owner) != TOKEN or len(mint.data) != 82:
                        raise ValueError('stateful_mint_decimals_unknown:' + state['mint'])
                    state['uiTokenAmount']['decimals'] = mint.data[44]
                    output.append(state)
        fee = result.meta().fee()
        value = {'err': None, 'fee': fee, 'loadedAddresses': loaded,
                 'preBalances': [a.lamports if a else 0 for a in before],
                 'postBalances': [a.lamports if a else 0 for a in after],
                 'preTokenBalances': pre_tokens, 'postTokenBalances': post_tokens,
                 'unitsConsumed': result.meta().compute_units_consumed()}
        observation = {'context': {'slot': self.anchor_slot}, 'value': value}
        cost = account_simulation(tx, tables, query, build, observation, fee)
        cost.update(evidence_class=DOMAIN, mainnet_feature_parity='UNVERIFIED',
                    execution_eligible=False, chain_fill=False)
        return {'cost': cost, 'simulation': observation, 'post': post,
                'before': before, 'keys': keys, 'metadata': result.meta()}

    def execute(self, tx, tables, query, build, expected_blockhash, *, precommit_check=None):
        message_hash = hashlib.sha256(bytes(tx)).hexdigest()
        if message_hash in self._committed:
            raise ValueError('stateful_duplicate_transaction')
        evidence = self.preview(tx, tables, query, build, expected_blockhash)
        # Virtual runtime re-checks authority and quote age AFTER preview, before
        # any local state mutation. Legacy technical probes remain unqualified.
        if precommit_check is not None:
            precommit_check()
        # This native local VM call has no network transport or signing callback.
        result = self._svm.send_transaction(tx)
        if isinstance(result, FailedTransactionMetadata):
            self.poisoned = True
            raise ValueError('stateful_commit_failed:' + str(result.err()))
        conflicts = [key for key, account in evidence['post'].items()
                     if fingerprint(self.account(key)) != fingerprint(account)]
        if result.fee() != evidence['metadata'].fee() or conflicts:
            self.poisoned = True
            raise ValueError('stateful_commit_mismatch:fees=' + str((result.fee(), evidence['metadata'].fee()))
                             + ':accounts=' + ','.join(conflicts))
        self._committed.add(message_hash)
        self._dirty.update(key for i, key in enumerate(evidence['keys'])
                           if fingerprint(self.account(key)) != fingerprint(evidence['before'][i]))
        return {'evidence_domain': DOMAIN, 'transaction_sha256': message_hash,
                'cost': evidence['cost'], 'units_consumed': result.compute_units_consumed(),
                'state_hash': self.state_hash(), 'signing': False, 'broadcast': False,
                'private_key_access': False, 'LIVE_PARITY_CLOSED': False}

    def state_hash(self):
        content = {key: fingerprint(self.account(key)) for key in sorted(set(self._accounts) | self._dirty)}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def checkpoint(self):
        """Evidence checkpoint only; no automatic replay or restart acceptance."""
        return {'domain': DOMAIN, 'anchor_slot': self.anchor_slot, 'wallet': self.wallet,
                'state_hash': self.state_hash(), 'committed': sorted(self._committed),
                'accounts': {key: json.loads(self.account(key).to_json()) if self.account(key) else None
                             for key in sorted(set(self._accounts) | self._dirty)},
                'program_hashes': self.program_hashes, 'poisoned': self.poisoned}
