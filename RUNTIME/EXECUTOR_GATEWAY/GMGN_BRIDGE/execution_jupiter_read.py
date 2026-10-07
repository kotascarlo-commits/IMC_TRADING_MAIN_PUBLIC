"""Jupiter quote/route input for the canonical execution adapter, read only."""
from datetime import datetime, timezone
import time

from execution_state import StateError, raw, decimal
from mykonos_meme_scout.jupiter_prepare import worker, rpc, SOL, USDC
from mykonos_meme_scout.jupiter_read import RATE_DB, rate_reservation
from mykonos_meme_scout.jupiter_route import verify, TOKEN
from mykonos_meme_scout.jupiter_simulation import associated


def account_snapshot(build, query, read_rpc, deadline):
    keys = list(dict.fromkeys([a['pubkey'] for a in build['swapInstruction']['accounts']]
        + [query['inputMint'], query['outputMint'], query['taker']]
        + [associated(query['taker'], mint, TOKEN) for mint in (query['inputMint'], query['outputMint'])]))
    if len(keys) > 64:
        raise StateError('jupiter_account_snapshot_unbounded')
    response = read_rpc('getMultipleAccounts', [keys, {'encoding': 'base64', 'commitment': 'finalized'}], deadline)
    if (type(response.get('context', {}).get('slot')) is not int or response['context']['slot'] <= 0
            or type(response.get('value')) is not list or len(response['value']) != len(keys)):
        raise StateError('jupiter_finalized_account_snapshot_unknown')
    return dict(zip(keys, response['value'])), response['context']['slot']


class JupiterExecutionReader:
    def __init__(self, store, *, fetch_build=None, rpc_reader=None, dex='Raydium CLMM'):
        self.store, self._rpc = store, rpc if rpc_reader is None else rpc_reader
        self.injected_rpc = rpc_reader is not None
        self.fetch_build = fetch_build
        self.dex = dex

    @property
    def rpc(self):
        from mykonos_meme_scout.jupiter_rpc_session import current_rpc_session
        return self._rpc if self.injected_rpc else current_rpc_session() or self._rpc

    def quote(self, event):
        status = self.store.status()
        if status['reconciliation_required'] or not status['wallet']:
            raise StateError('jupiter_quote_requires_reconciliation')
        amount = (int(decimal(status['wallet_balance_lamports']) * decimal(self.store.cfg['position_fraction']))
                  if event['side'] == 'BUY' else raw(event['amount_raw']))
        query = {'inputMint': SOL if event['side'] == 'BUY' else event['mint'],
                 'outputMint': event['mint'] if event['side'] == 'BUY' else SOL,
                 'amount': str(amount), 'taker': status['wallet'], 'slippageBps': 100,
                 'mode': 'fast', 'dexes': self.dex}
        deadline = time.monotonic() + 20
        if self.fetch_build is None:
            with rate_reservation(RATE_DB, deadline=deadline):
                # No quote exists while waiting for the shared rate bucket.
                # Anchor its age before the new HTTP request, after admission.
                requested = self.store.clock()
                deadline=min(deadline,time.monotonic()+self.store.cfg['quote_max_age_seconds'])
                if hasattr(self.rpc,'bind_deadline'):self.rpc.bind_deadline(deadline)
                build = worker({'operation': 'build', 'query': query}, deadline)
        else:
            requested = self.store.clock()
            build = self.fetch_build(query)
        accounts, slot = account_snapshot(build, query, self.rpc, deadline)
        proof = verify(build, query, accounts.get)
        wallet = accounts[query['taker']]
        if (not wallet or wallet['lamports'] != raw(status['wallet_balance_lamports'])
                or wallet.get('owner') != '11111111111111111111111111111111' or wallet.get('executable') is not False):
            raise StateError('jupiter_quote_wallet_changed')
        return {'source': 'JUPITER_V2_ORDER_QUOTE_OBSERVATION', 'event_id': event['event_id'],
                'wallet': query['taker'], 'input_token': query['inputMint'], 'output_token': query['outputMint'],
                'input_raw': query['amount'], 'minimum_output_raw': build['otherAmountThreshold'],
                'slippage_bps': build['slippageBps'], 'price_impact_bps': str(decimal(build['priceImpactPct'])*10000),
                'price_deviation_bps': proof['price_deviation_bps'], 'liquidity_usd': proof['liquidity_usd'],
                'quoted_at': requested, 'route_verified': True, 'query': query, 'build': build,
                'route_identity': proof['route_identity'], 'compute_unit_price_micro_lamports': proof['compute_unit_price_micro_lamports'],
                'account_snapshot_slot': slot}

    def revalidate(self, event):
        quote = event['quote']; deadline = time.monotonic() + self.store.cfg['quote_max_age_seconds']
        accounts, slot = account_snapshot(quote['build'], quote['query'], self.rpc, deadline)
        proof = verify(quote['build'], quote['query'], accounts.get)
        height = self.rpc('getBlockHeight', [{'commitment': 'finalized'}], deadline)
        if (proof['route_identity'] != event['jupiter_prepared']['route_identity']
                or type(height) is not int or height >= event['jupiter_prepared']['last_valid_block_height']
                or slot < quote['account_snapshot_slot']
                or raw(accounts[event['wallet']]['lamports']) != raw(event['jupiter_prepared']['wallet_balance_lamports'])):
            raise StateError('jupiter_pre_send_route_blockhash_or_wallet_changed')
        return True
