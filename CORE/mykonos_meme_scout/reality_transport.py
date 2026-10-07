"""Read/build/unsigned-simulate only. No signer or broadcasting capability.

The live closed route verifier is deliberately unchanged. Unsupported tokens
or unfunded same-bank simulations remain unknown; no smaller-size extrapolation.
"""
import base64
from decimal import Decimal
from types import SimpleNamespace
import hashlib
import json
import time
import urllib.request

from .reality_run import ROOT, DOMAIN, digest
from .execution_rules import number as D, quote_failures
from .execution_config import load_execution_config
from .jupiter_read import public_wallet, rate_reservation, RATE_DB, unique
from .jupiter_prepare import worker, SOL, rpc
from .jupiter_route import verify, verify_transaction
from .jupiter_simulation import message_keys

from execution_policy import owner_gate
from execution_jupiter import JupiterTransactionBuilder
from execution_jupiter_read import account_snapshot
from execution_state import timestamp
from solders.message import to_bytes_versioned
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.pubkey import Pubkey


def authority_off():
    cfg = load_execution_config()
    gate = owner_gate()
    if not gate['valid'] or gate['value'] != 'OFF' or cfg['execution_authority_pct'] != 0 or any(
            cfg[k] is not False for k in ('signing', 'broadcast_enabled', 'live_trading_enabled', 'wallet_access')):
        raise ValueError('reality_requires_all_real_authority_off')
    return cfg


def public_json(url):
    # Fixed credential-free market endpoints only. Redirects cannot change origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('market_redirect_blocked')
    opener = urllib.request.build_opener(NoRedirect)
    requested = time.time()
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'IMC-Readonly/1'}), timeout=5) as response:
        payload = response.read(65537)
        if len(payload) > 65536 or response.status != 200:
            raise ValueError('market_response_unknown')
    value = json.loads(payload, object_pairs_hook=unique)
    return value, requested, time.time()


def fx_quote():
    url = 'https://api.kraken.com/0/public/Ticker?pair=SOLEUR,EURUSD'
    value, started, received = public_json(url)
    clock, _, clock_received = public_json('https://api.kraken.com/0/public/Time')
    if value.get('error') != [] or clock.get('error') != []:
        raise ValueError('fx_market_error')
    server = clock['result']['unixtime']
    if not 0 <= clock_received-started <= 10 or abs(clock_received-server) > 10:
        raise ValueError('fx_time_unknown')
    pairs = value['result']
    sol = pairs['SOLEUR']
    eur = pairs.get('ZEURZUSD', pairs.get('EURUSD'))
    bid, ask, usd_bid, usd_ask = (D(sol['b'][0]), D(sol['a'][0]), D(eur['b'][0]), D(eur['a'][0]))
    if not 0 < bid <= ask or not 0 < usd_bid <= usd_ask:
        raise ValueError('fx_spread_unknown')
    return {'source': url, 'timestamp_semantics': 'HTTP_OBSERVATION_NOT_LAST_TRADE_TIME',
        'requested_at': started, 'received_at': received, 'server_time': server,
        'sol_eur_bid': str(bid), 'sol_eur_ask': str(ask),
        'usd_per_eur_mid': str((usd_bid+usd_ask)/2)}


def validate_evidence(claim, evidence, observed_at):
    if (evidence.get('evidence_domain') != DOMAIN or evidence.get('chain_fill') is not False
            or evidence.get('signing') is not False or evidence.get('broadcast') is not False
            or any(evidence.get(k) != claim[k] for k in ('claim_id', 'request_id', 'trade_id', 'input_raw'))
            or evidence.get('observation_kind') != 'RECEIPT'
            or evidence.get('side') != claim['side'] or evidence.get('cost_verified') is not True
            or evidence.get('route_verified') is not True):
        raise ValueError('shadow_evidence_binding_unknown')
    validate_unsigned(claim,evidence,observed_at)


def validate_mark(trade,evidence,observed_at):
    wallet=trade['receipts'][0]['wallet']
    binding={'wallet':wallet,'mint':trade['mint'],'side':'SELL','input_raw':trade['remaining_raw'],
        'fee_limit_lamports':trade['receipts'][0]['fee_limit_lamports']}
    if (evidence.get('observation_kind')!='MARK' or evidence.get('trade_id')!=trade['trade_id']
            or evidence.get('evidence_domain')!=DOMAIN or evidence.get('chain_fill') is not False
            or evidence.get('signing') is not False or evidence.get('broadcast') is not False
            or evidence.get('cost_verified') is not True or evidence.get('route_verified') is not True
            or evidence.get('input_raw')!=trade['remaining_raw']):
        raise ValueError('shadow_mark_unbound')
    validate_unsigned(binding,evidence,observed_at)


def validate_unsigned(claim,evidence,observed_at):
    quote = evidence['quote']
    limit=D(claim['fee_limit_lamports'])
    if (limit!=int(limit) or not 0<limit<=3_000_000
            or evidence.get('fee_limit_lamports')!=claim['fee_limit_lamports']
            or int(evidence['network_lamports'])>limit):
        raise ValueError('shadow_network_fee_limit_exceeded')
    if (quote['query']['taker'] != claim['wallet'] or quote['query']['inputMint'] != (SOL if claim['side']=='BUY' else claim['mint'])
            or quote['query']['outputMint'] != (claim['mint'] if claim['side']=='BUY' else SOL)):
        raise ValueError('shadow_wallet_or_mint_mismatch')
    if (not 0 <= observed_at-evidence['observed_at'] <= 2
            or not 0 <= observed_at-quote['quoted_at'] <= 2
            or not 0 <= observed_at-evidence['fx']['requested_at'] <= 15):
        raise ValueError('shadow_evidence_stale')
    tx = VersionedTransaction.from_bytes(base64.b64decode(evidence['unsigned_transaction'], validate=True))
    tables = [AddressLookupTableAccount(Pubkey.from_string(t['key']), [Pubkey.from_string(a) for a in t['addresses']])
              for t in evidence['tables']]
    verify_transaction(quote['build'], quote['query'], tables, tx, evidence['compute_unit_limit'])
    keys, _ = message_keys(tx, tables)
    if (tx.signatures != [Signature.default()]
            or hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest() != evidence['message_sha256']
            or digest(keys) != evidence['account_set_hash']
            or evidence['route_identity'] != quote['route_identity']
            or evidence['route_identity'] != digest({'query': quote['query'], 'build': quote['build']})
            or evidence['input_raw'] != quote['input_raw']
            or evidence['output_raw'] != quote['minimum_output_raw']
            or evidence['output_decimals'] not in (6, 9)
            or not 0 < evidence['compute_unit_limit'] <= 1_400_000
            or not 0 < evidence['units_consumed'] <= evidence['compute_unit_limit']
            or evidence['block_height'] >= evidence['last_valid_block_height']
            or int(evidence['network_lamports']) != 5000+(evidence['compute_unit_limit']*evidence['priority_micro_lamports']+999999)//1000000
            or not 0 < int(evidence['network_lamports']) <= 3_000_000):
        raise ValueError('shadow_route_message_cost_mismatch')
    cost=evidence['same_bank_cost_evidence']
    fee=int(evidence['network_lamports'])
    if (cost['status']!='SIMULATED_COST_OBSERVED' or cost['message_sha256']!=evidence['message_sha256']
            or cost['wallet']!=claim['wallet'] or int(cost['network_fee_lamports'])!=fee
            or int(cost['output_raw'])<int(evidence['output_raw'])
            or D(evidence['network_eur']) != D(fee)/10**9*D(evidence['fx']['sol_eur_ask'])
            or D(evidence['other_eur']) != D(cost['net_bound_account_lamports'])/10**9*D(evidence['fx']['sol_eur_ask'])):
        raise ValueError('shadow_same_bank_cost_mismatch')
    for key in ('network_eur', 'slippage_eur'):
        if D(evidence[key]) < 0:
            raise ValueError('shadow_cost_unknown')


class RealityTransport:
    def __init__(self):
        self.cfg = authority_off()
        self.wallet = public_wallet()
        # Builder uses only cfg/clock, never a send permit or a live ledger.
        self.builder = JupiterTransactionBuilder(SimpleNamespace(cfg=self.cfg, clock=timestamp))

    def quote(self, side, mint, amount):
        authority_off()
        query = {'inputMint': SOL if side == 'BUY' else mint, 'outputMint': mint if side == 'BUY' else SOL,
            'amount': str(amount), 'taker': self.wallet, 'slippageBps': 100,
            'mode': 'fast', 'dexes': 'Raydium CLMM'}
        deadline = time.monotonic()+20
        with rate_reservation(RATE_DB, deadline=deadline):
            requested = time.time()
            deadline = time.monotonic()+self.cfg['quote_max_age_seconds']
            build = worker({'operation': 'build', 'query': query}, deadline)
        from .jupiter_rpc_session import current_rpc_session
        reader = current_rpc_session() or rpc
        accounts, slot = account_snapshot(build, query, reader, deadline)
        proof = verify(build, query, accounts.get)
        quote = {'quoted_at': requested, 'query': query, 'build': build,
            'route_identity': proof['route_identity'], 'account_snapshot_slot': slot,
            'input_raw': str(amount), 'minimum_output_raw': build['otherAmountThreshold'],
            'slippage_bps': build['slippageBps'], 'price_impact_bps': str(D(build['priceImpactPct'])*10000),
            'price_deviation_bps': proof['price_deviation_bps'], 'liquidity_usd': proof['liquidity_usd'],
            'compute_unit_price_micro_lamports': proof['compute_unit_price_micro_lamports']}
        failures = quote_failures(quote, self.cfg)
        if failures or not 0 <= time.time()-requested <= self.cfg['quote_max_age_seconds']:
            raise ValueError('shadow_quote_risk:'+','.join(failures or ['stale_quote']))
        return quote

    def execute(self, claim, quote, fx, output_mid_eur):
        evidence=self._build_observation(claim,quote,fx,output_mid_eur,kind='RECEIPT')
        validate_evidence(claim,evidence,time.time())
        return evidence

    def mark(self,trade,quote,fx):
        binding={'wallet':self.wallet,'mint':trade['mint'],'side':'SELL','input_raw':trade['remaining_raw'],
            'trade_id':trade['trade_id'],'evidence_domain':DOMAIN,
            'fee_limit_lamports':trade['receipts'][0]['fee_limit_lamports']}
        evidence=self._build_observation(binding,quote,fx,(D(fx['sol_eur_bid'])+D(fx['sol_eur_ask']))/2,kind='MARK')
        validate_mark(trade,evidence,time.time())
        return evidence

    def _build_observation(self, claim, quote, fx, output_mid_eur, *,kind):
        authority_off()
        prepared = self.builder.build({'wallet': self.wallet}, quote)
        proof = prepared.evidence
        authority_off()
        tx = prepared.transaction
        tables = list(prepared.tables)
        keys, _ = message_keys(tx, tables)
        other_lamports = int(proof['same_bank_cost_evidence']['net_bound_account_lamports'])
        rate = D(fx['sol_eur_ask'])
        output_decimals = 9 if claim['side'] == 'SELL' else 6 # closed verifier supports SOL/USDC only
        slip = (D(quote['build']['outAmount'])-D(quote['minimum_output_raw']))/(Decimal(10)**output_decimals)*D(output_mid_eur)
        evidence = {**claim, 'side': claim['side'], 'observed_at': time.time(),
            'observation_kind':kind,
            'chain_fill': False, 'signing': False, 'broadcast': False, 'cost_verified': proof['cost_verified'],
            'route_verified': proof['full_route_semantics_verified'], 'quote': quote,
            'route_identity': quote['route_identity'], 'message_sha256': proof['message_sha256'],
            'account_set_hash': digest(keys), 'unsigned_transaction': base64.b64encode(bytes(tx)).decode(),
            'tables': [{'key': str(t.key), 'addresses': [str(a) for a in t.addresses]} for t in tables],
            'compute_unit_limit': proof['compute_unit_limit'], 'priority_micro_lamports': proof['compute_unit_price_micro_lamports'],
            'units_consumed': proof['units_consumed'], 'initial_units_consumed': proof['initial_units_consumed'],
            'same_bank_cost_evidence': proof['same_bank_cost_evidence'],
            'block_height': proof['block_height'], 'last_valid_block_height': proof['last_valid_block_height'],
            'network_lamports': proof['network_fee_lamports'], 'output_raw': quote['minimum_output_raw'],
            'output_decimals': output_decimals, 'network_eur': str(D(proof['network_fee_lamports'])/10**9*rate),
            'other_eur': str(Decimal(other_lamports)/10**9*rate), 'slippage_eur': str(slip), 'fx': fx,
            'fill_model': 'MINIMUM_OUTPUT_AFTER_NEW_DELAYED_QUOTE_AND_UNSIGNED_SIMULATION',
            'price_impact_eur': None, 'cost_partition': 'SLIPPAGE_SUBSET_OF_EXECUTION_NOT_ADDITIVE'}
        return evidence
