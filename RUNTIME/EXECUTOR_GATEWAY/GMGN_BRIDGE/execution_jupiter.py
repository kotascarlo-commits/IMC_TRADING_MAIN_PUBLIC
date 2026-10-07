"""Jupiter V2 integration in the existing Claim/Risk/Send/Receipt ledger.

Build is performed after the durable claim. ISOLATED transport is local only,
with disposable keys and cryptographically verifiable receipts. Production
transport rejects that domain and enforces every real gate independently.
"""
import base64
from dataclasses import dataclass
import hashlib
import time
import threading

from solders.message import to_bytes_versioned
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from execution_state import StateError, canonical, age, raw
from execution_jupiter_evidence import keys_digest, verify_receipt
from execution_validation import require, status as validation_status
from mykonos_meme_scout.jupiter_simulation import message_keys, account_simulation
from mykonos_meme_scout.jupiter_route import digest, verify_transaction

_SEND_SCOPE=threading.local()


@dataclass(frozen=True)
class PreparedJupiter:
    transaction: object
    tables: tuple
    evidence: dict


def bind_build(store, c, event, prepared):
    if (type(prepared) is not PreparedJupiter or event.get('provider') != 'JUPITER_V2'
            or event.get('jupiter_prepared') is not None or event.get('send_started_at') is not None
            or event.get('order_id')):
        raise StateError('jupiter_build_already_bound_or_unknown')
    proof = prepared.evidence
    tx = prepared.transaction
    quote = event['quote']
    verify_transaction(quote['build'], quote['query'], list(prepared.tables), tx, proof['compute_unit_limit'])
    keys, _ = message_keys(tx, list(prepared.tables))
    if (proof['route_identity'] != quote['route_identity'] or proof['route_identity'] != digest(
            {'query': quote['query'], 'build': quote['build']})
            or proof['wallet'] != event['wallet'] or proof['account_keys_sha256'] != keys_digest(keys)
            or proof['message_sha256'] != hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()
            or proof.get('full_route_semantics_verified') is not True
            or proof.get('cost_verified') is not True):
        raise StateError('jupiter_build_route_binding_mismatch')
    payload = {'provider': 'JUPITER_V2', 'contract_version': 2,
               **{k: proof[k] for k in ('message_sha256', 'account_keys_sha256')},
               **{k: quote[k] for k in ('wallet', 'input_token', 'output_token', 'input_raw', 'minimum_output_raw')}}
    event.update(jupiter_prepared=proof, unsigned_transaction=base64.b64encode(bytes(tx)).decode(),
                 request_payload_json=canonical(payload), payload_hash=digest(payload))
    store.put(c, 'events', event['event_id'], event)
    store.audit(c, 'jupiter_transaction_bound', {'event_id': event['event_id'], 'claim_id': event['claim_id'],
                'request_id': event['request_id'], 'route_identity': proof['route_identity'],
                'message_sha256': proof['message_sha256']}, event['trade_id'])
    return event


def bind_signature(store, event_id, signed):
    if type(signed) is not VersionedTransaction:
        raise StateError('jupiter_signed_transaction_unknown')
    signed.verify_and_hash_message()
    with store.transaction() as c:
        event = store.get(c, 'events', event_id)
        if (not event or event['state'] != 'SENT' or event.get('send_started_at') is not None
                or event.get('order_id') or event.get('signature_bound_at')):
            raise StateError('jupiter_signature_already_bound')
        original = VersionedTransaction.from_bytes(base64.b64decode(event['unsigned_transaction'], validate=True))
        if to_bytes_versioned(signed.message) != to_bytes_versioned(original.message) or len(signed.signatures) != 1:
            raise StateError('jupiter_signature_message_mismatch')
        signature = str(signed.signatures[0])
        if signed.signatures[0] == Signature.default() or any(
                e.get('order_id') == signature for e in store.all(c, 'events')):
            raise StateError('jupiter_signature_reused')
        payload = __import__('json').loads(event['request_payload_json'])
        event.update(order_id=signature, signature_bound_at=store.clock(),
                     jupiter_binding=dict(payload, signature=signature),
                     signed_transaction=base64.b64encode(bytes(signed)).decode())
        store.put(c, 'events', event_id, event)
        store.audit(c, 'jupiter_signature_bound_before_send', {'event_id': event_id, 'signature': signature,
                    'claim_id': event['claim_id'], 'request_id': event['request_id']}, event['trade_id'])
        return event


def validate_persisted_event(store,c,event):
    """Restart cannot relabel a provider, claim, route, message or receipt."""
    from execution_state import identity
    contract=require(store,c)
    if (not contract or event.get('evidence_domain')!=contract['domain'] or event.get('adapter_contract_version')!=2
            or event.get('claim_id')!=identity('execution-claim',event['event_id'])
            or event.get('request_id')!=identity('execution-request',event['event_id'])
            or event.get('quote',{}).get('source')!='JUPITER_V2_ORDER_QUOTE_OBSERVATION'
            or hashlib.sha256(event.get('request_payload_json','').encode()).hexdigest()!=event.get('payload_hash')):
        raise StateError('jupiter_persisted_claim_identity_corrupt')
    prepared=event.get('jupiter_prepared')
    if prepared is None:
        if event.get('order_id') or event.get('send_started_at'):
            raise StateError('jupiter_persisted_send_without_build')
        return
    quote=event['quote']
    if prepared['route_identity']!=digest({'query':quote['query'],'build':quote['build']}):
        raise StateError('jupiter_persisted_route_identity_corrupt')
    tx=VersionedTransaction.from_bytes(base64.b64decode(event['unsigned_transaction'],validate=True))
    if tx.signatures!=[Signature.default()] or hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest()!=prepared['message_sha256']:
        raise StateError('jupiter_persisted_unsigned_message_corrupt')
    if event.get('order_id'):
        signed=VersionedTransaction.from_bytes(base64.b64decode(event['signed_transaction'],validate=True))
        signed.verify_and_hash_message()
        if str(signed.signatures[0])!=event['order_id'] or to_bytes_versioned(signed.message)!=to_bytes_versioned(tx.message):
            raise StateError('jupiter_persisted_signed_message_corrupt')
    if event.get('send_started_at'):
        from execution_jupiter_evidence import binding
        binding(event,now=store.clock())


def require_pre_send(store, c, event):
    contract = require(store, c)
    mode = validation_status(store, c)
    if not contract or mode['aborted'] or mode['stopped'] or contract['domain'] != event.get('evidence_domain'):
        raise StateError('jupiter_validation_stopped_or_domain_unknown')
    if contract['domain']=='FINALIZED_CHAIN' and not mode['started']:
        raise StateError('jupiter_validation_not_started')
    proof = event.get('jupiter_prepared', {})
    if (event.get('state') != 'SENT' or proof.get('cost_verified') is not True
            or proof.get('full_route_semantics_verified') is not True or not event.get('signature_bound_at')
            or not event.get('order_id') or proof.get('route_identity') != event['quote']['route_identity']):
        raise StateError('jupiter_pre_send_incomplete')
    quote = event['quote']
    if age(quote['quoted_at'], store.clock()) > store.cfg['quote_max_age_seconds']:
        raise StateError('jupiter_pre_send_stale_quote')
    if age(proof['built_at'], store.clock()) > store.cfg['quote_max_age_seconds']:
        raise StateError('jupiter_pre_send_stale_transaction')
    tx = VersionedTransaction.from_bytes(base64.b64decode(event['signed_transaction'], validate=True))
    tx.verify_and_hash_message()
    payload = __import__('json').loads(event['request_payload_json'])
    if (str(tx.signatures[0]) != event['order_id'] or payload != {
            k: v for k, v in event['jupiter_binding'].items() if k != 'signature'}
            or digest(payload) != event['payload_hash']
            or hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest() != payload['message_sha256']
            or str(tx.message.recent_blockhash) != proof['blockhash']
            or proof['block_height'] >= proof['last_valid_block_height']
            or not 0 < proof['compute_unit_limit'] <= 1_400_000
            or not 0 < raw(proof['network_fee_lamports']) <= raw(store.cfg['max_order_fee_lamports'])
            or raw(proof['network_fee_lamports']) != 5000 + (
                proof['compute_unit_limit'] * proof['compute_unit_price_micro_lamports'] + 999999) // 1000000):
        raise StateError('jupiter_pre_send_message_fee_or_blockhash_mismatch')
    capital = (raw(event['actual_input_raw']) if event['side'] == 'BUY' else 0)
    if raw(proof['wallet_balance_lamports']) != raw(store.meta(c, 'wallet_balance_lamports')):
        raise StateError('jupiter_pre_send_wallet_baseline_changed')
    if capital + raw(proof['total_cost_lamports']) + raw(store.cfg['reserve_lamports']) > raw(
            store.meta(c, 'wallet_balance_lamports')):
        raise StateError('jupiter_pre_send_capital_or_rent_limit')
    if event['side'] == 'SELL':
        trade = store.get(c, 'trades', event['trade_id'])
        if raw(trade['remaining_raw']) != raw(event['actual_input_raw']):
            raise StateError('jupiter_pre_send_sell_remainder_mismatch')


class JupiterTransactionBuilder:
    """Wallet-bound, fresh blockhash/CU build; all I/O remains read/simulation."""
    def __init__(self, store, *, rpc_reader=None):
        from mykonos_meme_scout.jupiter_prepare import rpc
        self.store, self.rpc = store, rpc if rpc_reader is None else rpc_reader
        self.injected_rpc = rpc_reader is not None

    def build(self, event, quote):
        from mykonos_meme_scout.jupiter_rpc_session import current_rpc_session
        session=current_rpc_session()
        read=self.rpc if self.injected_rpc or session is None else session
        from mykonos_meme_scout.jupiter_prepare import (load_tables, two_phase_simulation, assemble, rpc)
        deadline = time.monotonic() + min(20, self.store.cfg['request_timeout_seconds'])
        from execution_jupiter_read import account_snapshot
        from mykonos_meme_scout.jupiter_route import verify
        accounts, route_slot = account_snapshot(quote['build'], quote['query'], read, deadline)
        route = verify(quote['build'], quote['query'], accounts.get)
        if route['route_identity'] != quote['route_identity'] or route_slot < quote['account_snapshot_slot']:
            raise StateError('jupiter_build_route_changed')
        tables = load_tables(quote['build'], deadline, rpc_reader=read)
        tx, simulation, fee, latest, measurement = two_phase_simulation(
            lambda blockhash, limit: assemble(quote['build'], quote['query'], tables, blockhash,
                                             compute_unit_limit=limit), deadline, rpc_reader=read)
        if not measurement['passed']:
            raise StateError('jupiter_simulation_failed')
        costs = account_simulation(tx, tables, quote['query'], quote['build'], simulation, fee)
        keys, _ = message_keys(tx, tables)
        height = read('getBlockHeight', [{'commitment': 'finalized'}], deadline)
        return PreparedJupiter(tx, tuple(tables), {
            'wallet': event['wallet'], 'built_at': self.store.clock(), 'route_identity': quote['route_identity'],
            'message_sha256': hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest(),
            'account_keys_sha256': keys_digest(keys), 'full_route_semantics_verified': True,
            'cost_verified': costs['status'] == 'SIMULATED_COST_OBSERVED', 'blockhash': str(tx.message.recent_blockhash),
            'last_valid_block_height': latest['lastValidBlockHeight'], 'block_height': height,
            'compute_unit_limit': measurement['limit'],
            'initial_units_consumed': measurement['initial_units'],
            'units_consumed': simulation['value']['unitsConsumed'],
            'same_bank_cost_evidence': costs,
            'compute_unit_price_micro_lamports': quote['compute_unit_price_micro_lamports'],
            'network_fee_lamports': str(fee),
            'total_cost_lamports': str(fee + max(0, int(costs['net_bound_account_lamports']))),
            'wallet_balance_lamports': costs['wallet_before_lamports'],
            'simulation_slot': simulation['context']['slot'], 'route_slot': route_slot})

class IsolatedJupiterTransport:
    """Strictly local bank/transport interface. Cannot call a network sender."""
    def __init__(self, store, bank):
        self.store, self.bank = store, bank
        self.calls = []
        from execution_jupiter_read import JupiterExecutionReader
        self.reader = JupiterExecutionReader(store, rpc_reader=bank.rpc)

    def sign(self, event):
        if event.get('evidence_domain') != 'ISOLATED':
            raise StateError('isolated_signing_domain_required')
        return self.bank.sign(event)

    def send_with_guard(self, contract, *, send_guard):
        if contract.get('evidenceDomain') != 'ISOLATED' or contract.get('provider') != 'JUPITER_V2':
            raise StateError('isolated_send_domain_required')
        with send_guard() as event:
            if event.get('evidence_domain') != 'ISOLATED' or event['order_id'] != contract['signature']:
                raise StateError('isolated_send_identity_mismatch')
            self.reader.revalidate(event)
            if age(event['quote']['quoted_at'], self.store.clock()) > self.store.cfg['quote_max_age_seconds']:
                raise StateError('jupiter_final_boundary_stale')
            self.calls.append(contract['requestId'])
            self.bank.send(event)
        return {'state': 'ACKNOWLEDGED', 'order_id': contract['signature'],
                **{key: contract[value] for key, value in (
                    ('eventId', 'eventId'), ('requestId', 'requestId'), ('tradeId', 'tradeId'), ('payloadHash', 'payloadHash'))}}


class JupiterRPCTransport:
    """Future owner-authorized sender; one attempt, no retry or fee fallback.

    Signer must be an owned local capability; it receives a validated message,
    never exports a key. Current configuration forbids invoking that capability.
    """
    def __init__(self, store, signer=None, rpc_sender=None):
        self.store, self.signer, self.rpc_sender = store, signer or OwnedWalletSigner(), rpc_sender or send_rpc_once
        from execution_jupiter_read import JupiterExecutionReader
        self.reader = JupiterExecutionReader(store)

    def available(self):
        from execution_state import ROOT
        return (ROOT/'TOOLS/imc_local_wallet_verify.py').is_file()

    def real_gates(self):
        from execution_policy import owner_gate
        from execution_state import load_config
        gate = owner_gate()
        if (not gate['valid'] or gate['value'] != 'ON' or not all(self.store.cfg[k] is True for k in
                ('signing', 'broadcast_enabled', 'live_trading_enabled')) or load_config()!=dict(self.store.cfg)):
            raise StateError('jupiter_real_gates_off')

    def sign(self, event):
        if event.get('evidence_domain') != 'FINALIZED_CHAIN':
            raise StateError('production_rejects_isolated_signing')
        self.real_gates()
        result = self.signer(event)
        self.real_gates()
        return result

    def send_with_guard(self, contract, *, send_guard):
        if contract.get('evidenceDomain') != 'FINALIZED_CHAIN':
            raise StateError('production_rejects_isolated_permit')
        self.real_gates()
        with send_guard() as event:
            self.reader.revalidate(event)
            if (age(event['quote']['quoted_at'], self.store.clock()) > self.store.cfg['quote_max_age_seconds']
                    or time.time()*1000 >= contract['expiresAtMs']):
                raise StateError('jupiter_final_boundary_stale')
            self.real_gates()
            # No helper can select another transaction, retry, replace a
            # blockhash, increase fees, or sign inside the SEND operation.
            _SEND_SCOPE.permit=(contract,event)
            try:
                result = self.rpc_sender('sendTransaction', [event['signed_transaction'], {
                    'encoding': 'base64', 'skipPreflight': False, 'preflightCommitment': 'finalized', 'maxRetries': 0}])
            finally:_SEND_SCOPE.permit=None
        if result != contract['signature']:
            raise StateError('jupiter_send_ack_signature_mismatch')
        return {'state': 'ACKNOWLEDGED', 'order_id': result,
                **{key: contract[key] for key in ('eventId', 'requestId', 'tradeId', 'payloadHash')}}


class OwnedWalletSigner:
    def __call__(self, event):
        import subprocess, sys, json
        from execution_state import ROOT
        request = {k:event[k] for k in ('event_id','request_id','payload_hash')}
        result = subprocess.run([str(__import__('pathlib').Path(sys._base_executable).resolve(strict=True)), '-I',
            str(ROOT/'TOOLS/imc_local_wallet_verify.py'),'--sign-jupiter-event'], input=canonical(request),
            capture_output=True,text=True,timeout=2,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or len(result.stdout)>4096:
            raise StateError('owned_wallet_signing_unavailable')
        response=json.loads(result.stdout)
        if set(response)!={'signed_transaction'}:
            raise StateError('owned_wallet_signing_response_unknown')
        return VersionedTransaction.from_bytes(base64.b64decode(response['signed_transaction'],validate=True))


def send_rpc_once(method, params):
    """Exactly one bounded HTTPS request, no redirect, retries or write CLI."""
    import http.client, json
    permit=getattr(_SEND_SCOPE,'permit',None)
    _SEND_SCOPE.permit=None  # Consume before any possible socket I/O.
    if permit is None:raise StateError('jupiter_owned_send_scope_required')
    contract,event=permit
    from execution_policy import owner_gate
    from execution_state import load_config
    cfg=load_config()
    from mykonos_meme_scout.jupiter_rpc_session import current_rpc_session
    session=current_rpc_session()
    if session is None:raise StateError('jupiter_quote_session_required')
    session.fresh()
    if (contract['evidenceDomain']!='FINALIZED_CHAIN' or event['evidence_domain']!='FINALIZED_CHAIN'
            or time.time()*1000>=contract['expiresAtMs'] or params[0]!=event['signed_transaction']
            or owner_gate()!={'value':'ON','valid':True}
            or not all(cfg[k] is True for k in ('signing','broadcast_enabled','live_trading_enabled'))):
        raise StateError('jupiter_owned_send_scope_changed')
    if (method!='sendTransaction' or len(params)!=2 or params[1]!={
            'encoding':'base64','skipPreflight':False,'preflightCommitment':'finalized','maxRetries':0}
            or not 0<len(base64.b64decode(params[0],validate=True))<=1232):
        raise StateError('jupiter_send_operation_blocked')
    connection=http.client.HTTPSConnection('api.mainnet-beta.solana.com',timeout=6)
    try:
        # Establish TLS before the final financial boundary. Gate/quote
        # changes during DNS/TLS must prevent the first signed request byte.
        connection.connect()
        session.fresh()
        if (owner_gate()!={'value':'ON','valid':True} or load_config()!=cfg
                or time.time()*1000>=contract['expiresAtMs']):
            raise StateError('jupiter_gates_changed_during_connect')
        connection.request('POST','/',canonical({'jsonrpc':'2.0','id':1,'method':method,'params':params}),
                           headers={'Content-Type':'application/json','Accept':'application/json'})
        response=connection.getresponse();body=response.read(65537)
        if response.status!=200 or len(body)>65536:
            raise StateError('jupiter_send_response_unknown')
        from execution_adapter import unique_pairs
        value=json.loads(body,object_pairs_hook=unique_pairs)
        if set(value)!={'jsonrpc','id','result'} or value['jsonrpc']!='2.0' or type(value['id']) is not int or value['id']!=1:
            raise StateError('jupiter_send_ack_unknown')
        return value['result']
    finally:connection.close()


def recover_observation(store, reader, snapshot):
    """Receipt-first read recovery; expected positions come from durable fills.

    A chain snapshot is never copied into the executor inventory to hide a
    mismatch. UNKNOWN remains reserved if its finalized receipt is absent.
    """
    events = store.status()['events']
    orders, fills, failures = {}, [], []
    for event in events:
        if event.get('provider') != 'JUPITER_V2' or not event.get('send_started_at'):
            continue
        result = reader.observe(event)
        orders[event['event_id']] = {'state': result['state'], 'order_id': event['order_id']}
        if result['state']=='FAILED':
            failures.append(result['proof'])
        if result['state'] == 'FILLED':
            proof = dict(result['proof'], evidence_domain=event['evidence_domain'])
            fills.append({'data': {'feedback_id': 'jupiter-recovery:' + event['order_id'],
                **{key: event[key] for key in ('event_id', 'trade_id', 'mint', 'side', 'order_id')},
                'signature': event['order_id'], 'status': 'FILLED'}, 'proof': proof})
    with store.transaction() as c:
        from execution_validation import foreign_inventory
        quantities = {mint: raw(amount) for mint, amount in foreign_inventory(store, c).items()}
        for trade in store.all(c, 'trades'):
            if raw(trade['remaining_raw']):
                quantities[trade['mint']] = quantities.get(trade['mint'], 0) + raw(trade['remaining_raw'])
        for fill in fills:
            if store.get(c, 'receipts', fill['proof']['signature']) is None:
                mint = fill['proof']['mint']
                quantities[mint] = quantities.get(mint, 0) + raw(fill['proof']['token_delta_raw'], signed=True)
    return dict(snapshot, executor_positions={k: str(v) for k, v in quantities.items() if v},
                orders=orders, recovered_fills=fills, failed_receipts=failures)


def record_failed_receipt(store,c,proof):
    event=store.get(c,'events',proof['event_id'])
    from execution_jupiter_evidence import binding
    bound=binding(event,now=store.clock())
    if (proof.get('source')!='JUPITER_FINALIZED_FAILED_EXACT_MESSAGE'
            or any(proof.get(k)!=bound[k] for k in ('signature','message_sha256','account_keys_sha256'))
            or proof.get('evidence_domain')!=event.get('evidence_domain')
            or any(raw(proof[k],signed=True)!=0 for k in ('token_delta_raw','swap_input_raw','swap_output_raw'))
            or raw(proof['wallet_delta_lamports'],signed=True)!=-raw(proof['fees_lamports'])):
        raise StateError('jupiter_failed_receipt_identity_mismatch')
    receipt={'event_id':event['event_id'],'proof':proof}
    old=store.get(c,'receipts',proof['signature'])
    if old is not None:
        if old!=receipt:raise StateError('jupiter_failed_receipt_conflict')
        return
    trade=store.get(c,'trades',event['trade_id'])
    trade['wallet_delta_lamports']=str(raw(trade['wallet_delta_lamports'],signed=True)-raw(proof['fees_lamports']))
    trade['fees_lamports']=str(raw(trade['fees_lamports'])+raw(proof['fees_lamports']))
    store.put(c,'trades',trade['trade_id'],trade)
    store.put(c,'receipts',proof['signature'],receipt)
    store._feedback(c,{'feedback_id':'jupiter-failed:'+proof['signature'],
        **{k:event[k] for k in ('event_id','trade_id','mint','side','order_id')},'status':'FAILURE'})
    store.audit(c,'failed_chain_receipt',receipt,trade['trade_id'])
