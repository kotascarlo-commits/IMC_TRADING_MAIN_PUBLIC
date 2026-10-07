"""Controlled BUY/SELL lifecycle through existing execution components.

No network, credentials, production gate, signing or broadcast. The isolated
venue journal supplies trusted request lookup; this does not claim GMGN offers
that recovery contract. The existing risk metrics fixture is not live evidence.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_execution import flow
from test_execution_adapter import ready
import test_execution_adapter as adapter_fixtures
from test_single_swap_transport import run as sdk_dispatch, TOKEN
from execution_adapter import SingleSendAdapter, sdk_ack_reply, SOL
from execution_evidence import ReconciliationReader, SolanaEvidence
from execution_policy import RiskEngine
from execution_positions import PositionManager
from execution_state import Ledger, StateError, canonical


class ControlledVenue:
    def __init__(self, flow, path):
        self.flow, self.path = flow, path
        self.path.write_text('{}')
        self.transactions = {}
        self.send_calls = []
        self.sdk_calls = []
        self.wallet, self.mint = SOL, TOKEN
        self.balance, self.quantity, self.slot = 1_000_000_000, 0, 42
        self.rpc = SolanaEvidence(flow.store.cfg, transport=self.rpc_read)
        self.reader = ReconciliationReader(flow.store.cfg, cli=self.read,
                                           rpc=self.rpc, clock=flow.clock)

    def records(self):
        return json.loads(self.path.read_text())

    def send(self, contract, *, lose_response=False):
        body = contract['payloadJson'].encode()
        payload = json.loads(body)
        self.send_calls.append('BUY' if payload['input_token'] == SOL else 'SELL')
        # Bind this fixture transport to the original persisted claim. Never
        # recover an order by payload similarity or the newest signal.
        with self.flow.store.transaction() as c:
            claim = self.flow.store.get(c, 'events', contract['eventId'])
        assert claim['state'] == 'SENT' and claim['send_started_at']
        assert set(contract) == {'eventId', 'requestId', 'tradeId', 'payloadHash',
                                 'payloadJson', 'issuedAtMs', 'expiresAtMs', 'responseUntilMs'}
        for field, key in [('eventId', 'event_id'), ('requestId', 'request_id'),
                           ('tradeId', 'trade_id'), ('payloadHash', 'payload_hash')]:
            assert contract[field] == claim[key]
        now_ms = int(self.flow.now.timestamp()) * 1000 + self.flow.now.microsecond // 1000
        assert contract['issuedAtMs'] <= now_ms < contract['expiresAtMs'] <= contract['responseUntilMs']
        assert contract['expiresAtMs'] - contract['issuedAtMs'] <= 2000
        assert contract['responseUntilMs'] - contract['issuedAtMs'] <= 30000
        with pytest.raises(TypeError):
            contract['requestId'] = 'mutated'
        assert claim['request_payload_json'].encode() == body
        assert hashlib.sha256(body).hexdigest() == claim['payload_hash']
        records = self.records()
        assert claim['request_id'] not in records, 'duplicate SEND'
        sdk_reply = sdk_dispatch('pythonContract', canonical(dict(contract)))
        self.sdk_calls.append(sdk_reply['requestId'])
        # The same production normalizer must reject a different request ACK.
        permit = SimpleNamespace(event_id=claim['event_id'], request_id=claim['request_id'],
                                 trade_id=claim['trade_id'], payload_hash=claim['payload_hash'])
        with pytest.raises(StateError, match='sdk_ack_identity_or_response_unknown'):
            sdk_ack_reply(permit, dict(sdk_reply, requestId='foreign-request'))
        record = {key: claim[key] for key in
                  ('request_id', 'event_id', 'trade_id', 'side', 'mint', 'wallet', 'payload_hash')}
        record.update(order_id=sdk_reply['order_id'], payload=payload, status='pending')
        records[claim['request_id']] = record
        self.path.write_text(canonical(records))  # durable before response loss
        if lose_response:
            raise TimeoutError('controlled response loss after SEND')
        return sdk_reply

    def recover_order(self, event, *, wrong_request=False):
        with self.flow.store.transaction() as c:
            claim = self.flow.store.get(c, 'events', event['event_id'])
        record = self.records()[claim['request_id']]
        if wrong_request:
            record = dict(record, request_id='foreign-request')
        if any(record[key] != claim[key] for key in
               ('request_id', 'event_id', 'trade_id', 'side', 'mint', 'wallet', 'payload_hash')):
            raise StateError('controlled_request_order_mismatch')
        assert self.flow.store.bind_order(event['event_id'], record['order_id'])

    def finalize(self, event):
        records = self.records()
        with self.flow.store.transaction() as c:
            claim = self.flow.store.get(c, 'events', event['event_id'])
        record = records[claim['request_id']]
        assert record['status'] == 'pending'
        before_balance, before_quantity = self.balance, self.quantity
        buy = record['side'] == 'BUY'
        amount = int(record['payload']['input_amount'])
        assert amount == (50_000_000 if buy else self.quantity)
        self.quantity = 1000 if buy else 0
        self.balance += -50_000_000 if buy else 55_000_000
        self.slot += 1
        signature = ('b' if buy else 's') * 88
        def tokens(quantity):
            return [] if not quantity else [{'mint': record['mint'], 'owner': record['wallet'],
                                             'uiTokenAmount': {'amount': str(quantity)}}]
        self.transactions[signature] = {'slot': self.slot,
            'blockTime': int(self.flow.now.timestamp()),
            'transaction': {'signatures': [signature], 'message': {'accountKeys': [
                {'pubkey': record['wallet'], 'signer': True}]}},
            'meta': {'err': None, 'fee': 5000,
                'preBalances': [before_balance], 'postBalances': [self.balance],
                'preTokenBalances': tokens(before_quantity), 'postTokenBalances': tokens(self.quantity)}}
        record.update(status='confirmed', state=30, hash=signature, report={
            'input_token': SOL if buy else record['mint'],
            'output_token': record['mint'] if buy else SOL,
            'input_amount': str(amount), 'output_amount': '1000' if buy else '55000000'})
        self.path.write_text(canonical(records))

    def read(self, args):
        if args[:2] == ['portfolio', 'info']:
            return {'wallets': [{'chain': 'sol', 'address': self.wallet}]}
        if args[:2] == ['portfolio', 'holdings']:
            return {'list': [] if not self.quantity else [
                {'token': {'token_address': self.mint}, 'balance': '0.001000'}], 'next': ''}
        assert args[:2] == ['order', 'get']
        return next(record for record in self.records().values() if record['order_id'] == args[-1])

    def rpc_read(self, method, params):
        if method == 'getTransaction':
            assert params[1]['commitment'] == 'finalized'
            return self.transactions.get(params[0])
        assert params[0] == self.wallet
        options = params[-1]
        assert options['commitment'] == 'finalized' and self.slot >= options['minContextSlot']
        if method == 'getBalance':
            return {'context': {'slot': self.slot}, 'value': self.balance}
        assert method == 'getTokenAccountsByOwner'
        program = params[1]['programId']
        entries = []
        if self.quantity and program.startswith('Tokenkeg'):
            entries = [{'pubkey': 'controlled-token-account', 'account': {
                'owner': program, 'executable': False, 'data': {'parsed': {
                    'type': 'account', 'info': {'owner': self.wallet, 'mint': self.mint,
                        'tokenAmount': {'decimals': 6, 'amount': str(self.quantity)}}}}}}]
        return {'context': {'slot': self.slot}, 'value': entries}

    def reconcile(self):
        return self.flow.store.reconcile(self.reader.snapshot(self.flow.store))


@pytest.mark.parametrize('recovery', ['normal', 'runtime_poll', 'buy_ack_restart', 'buy_unknown_late_fill',
                                     'position_restart', 'sell_unknown_late_fill'])
def test_controlled_signal_buy_sell_closed(flow, tmp_path, monkeypatch, recovery, request):
    production_gate = Path(__file__).parents[1] / 'config/owner-broadcast-gate.json'
    gate_before = production_gate.read_bytes()
    assert json.loads(gate_before)['OWNER_BROADCAST_GATE'] == 'OFF'
    # Valid addresses traverse the installed SDK without replacing signed bytes.
    flow.now = datetime.now(timezone.utc)
    flow.now = flow.now.replace(microsecond=flow.now.microsecond // 1000 * 1000)
    flow.store = Ledger(tmp_path / 'sdk-lifecycle.db', dict(flow.store.cfg), clock=flow.clock)
    flow.store.bootstrap(flow.clock())
    assert flow.store.reconcile({'wallet': SOL, 'observed_at': flow.clock(),
        'chain_positions': {}, 'executor_positions': {}, 'orders': {}, 'balance_lamports': '1000000000'})
    candidate, quote_fixture, equity_fixture = flow.candidate, flow.quote, adapter_fixtures.observe
    monkeypatch.setattr(flow, 'candidate', lambda **kwargs: candidate(**({'mint': TOKEN} | kwargs)))
    monkeypatch.setattr(flow, 'quote', lambda event, **kwargs: dict(quote_fixture(event, **kwargs), wallet=SOL))
    monkeypatch.setattr(adapter_fixtures, 'observe', lambda f: dict(equity_fixture(f), wallet=SOL))
    buy, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    if recovery == 'normal':
        with pytest.raises(StateError, match='ambiguous_execution_transport'):
            SingleSendAdapter(risk, probes, transport=lambda _: None, contract_transport=lambda _: None)
    venue = ControlledVenue(flow, tmp_path / 'controlled-venue.json')
    if recovery == 'runtime_poll':
        import threading
        from http.server import ThreadingHTTPServer
        import imc_pure_signal_gateway as gateway
        import gmgn_signal_bridge as bridge
        monkeypatch.setattr(gateway, '_LEDGER', flow.store)
        monkeypatch.setattr(gateway, '_POSITIONS', None)
        monkeypatch.setattr(gateway, '_QUOTE_RETRY_AT', 0)
        monkeypatch.setattr(gateway, 'DB', tmp_path / 'no-discovery-required.db')
        runtime_adapter = SingleSendAdapter(risk, probes, contract_transport=venue.send)
        monkeypatch.setattr(gateway, '_EXECUTION', runtime_adapter)
        monkeypatch.setattr(gateway, 'quote_event', lambda event: dict(flow.quote(event,
            minimum_output_raw='900' if event['side'] == 'BUY' else '50000000'), source='GMGN_ORDER_QUOTE'))
        monkeypatch.setenv('IMC_EXECUTION_TOKEN', 'isolated-runtime-poll-fixture')
        server = ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        def close_server():
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        request.addfinalizer(close_server)
        monkeypatch.setattr(bridge, 'gateway', lambda: 'http://127.0.0.1:' + str(server.server_port))
        import urllib.request
        import urllib.error
        injected = urllib.request.Request(bridge.gateway() + '/execute-next',
            data=json.dumps({'quote': quote}).encode(), method='POST',
            headers={'Content-Type':'application/json','Authorization':'Bearer isolated-runtime-poll-fixture'})
        with pytest.raises(urllib.error.HTTPError) as rejected:
            bridge.get_json(injected)
        assert rejected.value.code == 400 and venue.records() == {}
    if recovery == 'runtime_poll':
        original_read = venue.read
        def current_read(args):
            order = original_read(args)
            if args[:2] != ['order', 'get']:
                return order
            mapped = {k: order[k] for k in ('order_id', 'hash', 'state') if k in order}
            mapped.update(confirmation={'state': 'confirmed' if order['status'] == 'confirmed' else 'processed'},
                          from_address=order['wallet'], chain='sol')
            if order['status'] == 'confirmed':
                report = order['report']
                mapped.update(input_token=report['input_token'], output_token=report['output_token'],
                              filled_input_amount=report['input_amount'], filled_output_amount=report['output_amount'])
            return mapped
        venue.reader.cli = current_read
    # The existing fixture patches only this isolated ledger's quality status.
    # Preserve that test-only projection when constructing a restarted Ledger.
    def restart():
        nonlocal risk
        old = flow.store
        fixture_performance = old.status()['performance']
        flow.store = Ledger(old.path, clock=flow.clock)
        original = flow.store.status
        def status(**kwargs):
            result = original(**kwargs)
            result['performance'] = fixture_performance
            return result
        monkeypatch.setattr(flow.store, 'status', status)
        flow.store.recover()
        risk = RiskEngine(flow.store, gate_path=risk.gate_path, clock=flow.clock)

    def adapter(event, lose=False):
        if recovery == 'runtime_poll':
            return SimpleNamespace(execute=lambda event, quote: bridge.execute_next()['execution'])
        return SingleSendAdapter(risk, probes,
            contract_transport=lambda contract: venue.send(contract, lose_response=lose))

    result = adapter(buy, recovery == 'buy_unknown_late_fill').execute(buy, quote)
    assert result['state'] == ('UNKNOWN' if recovery == 'buy_unknown_late_fill' else 'ACKNOWLEDGED')
    assert flow.trade(buy)['remaining_raw'] == '0'  # even a lying ACK is no fill
    assert flow.trade(buy)['execution_state'] != 'POSITION_OPEN'
    request = flow.store.status()['events'][0]['request_id']
    assert request and len(venue.records()) == 1
    if recovery in {'buy_ack_restart', 'buy_unknown_late_fill'}:
        restart()
        assert flow.store.status()['events'][0]['request_id'] == request
    assert adapter(buy).execute(buy, quote)['state'] == ('NO_READY_EVENT' if recovery == 'runtime_poll' else 'BLOCKED')
    assert len(venue.records()) == 1 and flow.store.pending() is None
    assert venue.send_calls == ['BUY']
    assert flow.store.prepare_buy(flow.candidate(mint='blocked-other')) is None
    if recovery == 'buy_unknown_late_fill':
        with pytest.raises(StateError, match='request_order_mismatch'):
            venue.recover_order(buy, wrong_request=True)
        assert flow.store.status()['events'][0]['order_id'] is None
        venue.recover_order(buy)
    assert not venue.reconcile()  # bound order is pending, no invented fill
    assert flow.trade(buy)['remaining_raw'] == '0'
    venue.finalize(buy)
    observation = venue.reader.snapshot(flow.store)
    assert flow.trade(buy)['remaining_raw'] == '0'  # independent read is pure
    recovered = observation['recovered_fills'][0]
    assert flow.store.feedback(recovered['data'], recovered['proof'])
    assert flow.trade(buy)['execution_state'] == 'RECONCILING'
    assert flow.store.materialize_exit(buy['trade_id']) is None
    with flow.store.transaction() as c:
        assert flow.store.sell_anchor(c, flow.store.get(c, 'trades', buy['trade_id'])) is None
        prior_intent = flow.store.meta(c, 'position_exit:' + buy['trade_id'])
        if recovery in {'buy_ack_restart', 'buy_unknown_late_fill'}:
            assert prior_intent['reason'] == 'recovery_exit'
        else:
            assert prior_intent is None
    assert flow.store.prepare_buy(flow.candidate(mint='blocked-unreconciled')) is None
    assert flow.store.reconcile(observation)
    assert flow.trade(buy)['execution_state'] == 'POSITION_OPEN'
    assert venue.reconcile()  # duplicate evidence cannot book the BUY twice
    assert flow.trade(buy)['buy_raw'] == '1000'
    with flow.store.transaction() as c:
        existing_sells = [e for e in flow.store.all(c, 'events') if e['side'] == 'SELL']
        assert len(existing_sells) == (1 if recovery in {'buy_ack_restart', 'buy_unknown_late_fill'} else 0)
        flow.store.request_exit(c, flow.store.get(c, 'trades', buy['trade_id']), 'strategy_exit')
        exit_intent = flow.store.meta(c, 'position_exit:' + buy['trade_id'])
    if recovery == 'position_restart':
        restart()
        assert flow.trade(buy)['remaining_raw'] == '1000'
        with flow.store.transaction() as c:
            assert flow.store.meta(c, 'position_exit:' + buy['trade_id']) == exit_intent
        assert flow.store.prepare_buy(flow.candidate(mint='blocked-restart')) is None
        assert venue.reconcile()
    flow.now = datetime.now(timezone.utc)
    flow.now = flow.now.replace(microsecond=flow.now.microsecond // 1000 * 1000)
    manager = PositionManager(flow.store)
    assert manager.tick()['status'] == 'PASS'
    sell = flow.store.pending()
    confirmed_remaining = flow.trade(buy)['remaining_raw']
    assert sell['trade_id'] == buy['trade_id'] and sell['amount_raw'] == confirmed_remaining == '1000'
    assert flow.store.prepare_sell(buy['trade_id'], reason='strategy_exit')['event_id'] == sell['event_id']
    assert manager.tick()['status'] == 'PASS'
    assert flow.store.pending()['event_id'] == sell['event_id']
    with flow.store.transaction() as c:
        assert len([e for e in flow.store.all(c, 'events') if e['side'] == 'SELL']) == 1
        assert flow.store.meta(c, 'position_exit:' + buy['trade_id']) is not None
    sell_quote = dict(flow.quote(sell, minimum_output_raw='50000000'), source='GMGN_ORDER_QUOTE')
    assert adapter(sell).execute(sell, sell_quote)['state'] == 'ACKNOWLEDGED'
    assert flow.trade(sell)['remaining_raw'] == '1000'
    assert flow.trade(sell)['execution_state'] == 'SELL_ACKNOWLEDGED'
    if recovery == 'sell_unknown_late_fill':
        assert flow.store.feedback(dict(flow.data(sell, 'UNKNOWN_EXECUTION'),
                                      order_id=flow.store.status()['events'][-1]['order_id']))
        restart()
    assert adapter(sell).execute(sell, sell_quote)['state'] == ('NO_READY_EVENT' if recovery == 'runtime_poll' else 'BLOCKED')
    assert not venue.reconcile() and flow.trade(sell)['remaining_raw'] == '1000'
    venue.finalize(sell)
    observation = venue.reader.snapshot(flow.store)
    sell_fill = next(item for item in observation['recovered_fills']
                     if item['data']['event_id'] == sell['event_id'])
    assert flow.store.feedback(sell_fill['data'], sell_fill['proof'])
    assert flow.trade(sell)['execution_state'] == 'RECONCILING'
    assert flow.trade(sell)['real_net_pnl_lamports'] is None
    wrong_wallet = dict(observation, balance_lamports=str(venue.balance + 1))
    assert not flow.store.reconcile(wrong_wallet)
    assert flow.trade(sell)['execution_state'] != 'CLOSED'
    assert flow.store.reconcile(observation) and venue.reconcile()
    trade = flow.trade(sell)
    assert trade['execution_state'] == 'CLOSED' and trade['remaining_raw'] == '0'
    assert trade['real_net_pnl_lamports'] == '5000000' and trade['fees_lamports'] == '10000'
    records = list(venue.records().values())
    assert [r['side'] for r in records].count('BUY') == 1
    assert [r['side'] for r in records].count('SELL') == 1
    assert venue.send_calls == ['BUY', 'SELL']
    assert venue.sdk_calls == [next(record['request_id'] for record in records if record['side'] == side)
                               for side in ('BUY', 'SELL')]
    assert len({r['request_id'] for r in records}) == len({r['order_id'] for r in records}) == 2
    with flow.store.transaction() as c:
        assert len(flow.store.all(c, 'receipts')) == 2
    closed_status = flow.store.status()
    assert len(closed_status['events']) == 2
    assert {e['side'] for e in closed_status['events']} == {'BUY', 'SELL'}
    assert all(e['state'] == 'FILLED' for e in closed_status['events'])
    assert closed_status['reconciliation_required'] is False
    assert flow.store.pending() is None
    if recovery == 'normal':
        # Stale authorization at the actual dispatch boundary performs zero I/O.
        from test_execution_equity import observe
        flow.store.mark_equity(dict(observe(flow), wallet=SOL))
        event = flow.store.prepare_buy(flow.candidate(mint='stale-risk'))
        fresh_quote = dict(flow.quote(event), source='GMGN_ORDER_QUOTE')
        original = risk.authorize_send
        def delayed(*args):
            permit = original(*args)
            flow.now += timedelta(seconds=3)
            return permit
        monkeypatch.setattr(risk, 'authorize_send', delayed)
        assert adapter(event).execute(event, fresh_quote)['state'] == 'UNKNOWN'
        assert len(venue.records()) == 2
        assert venue.send_calls == ['BUY', 'SELL']
    assert production_gate.read_bytes() == gate_before
