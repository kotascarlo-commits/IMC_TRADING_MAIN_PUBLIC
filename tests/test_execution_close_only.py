"""Owner OFF exits only a proved owner-opened remainder; dummy SDK only."""
import json
from pathlib import Path

import pytest
from test_execution import flow
from test_execution_adapter import ready
import test_execution_adapter as fixtures
from test_execution_lifecycle_e2e import ControlledVenue, TOKEN
from test_single_swap_transport import SDK
from execution_process_lifecycle_worker import fixture_now
from execution_adapter import SingleSendAdapter, SOL
from execution_transport import SDKContractTransport
from execution_state import Ledger, canonical


@pytest.mark.parametrize('case', ['close', 'deny_unproved_or_unreconciled'])
def test_owner_off_existing_sdk_lifecycle(flow, tmp_path, monkeypatch, case):
    production = Path(__file__).parents[1] / 'config/owner-broadcast-gate.json'
    original_gate = production.read_bytes()
    flow.now = fixture_now()
    flow.store = Ledger(tmp_path / 'close-only.db', dict(flow.store.cfg), clock=flow.clock)
    flow.store.bootstrap(flow.clock())
    assert flow.store.reconcile({'wallet': SOL, 'observed_at': flow.clock(), 'chain_positions': {},
        'executor_positions': {}, 'orders': {}, 'balance_lamports': '1000000000'})
    candidate, quote, equity = flow.candidate, flow.quote, fixtures.observe
    monkeypatch.setattr(flow, 'candidate', lambda **kw: candidate(**({'mint': TOKEN} | kw)))
    monkeypatch.setattr(flow, 'quote', lambda event, **kw: dict(quote(event, **kw), wallet=SOL))
    monkeypatch.setattr(fixtures, 'observe', lambda item: dict(equity(item), wallet=SOL))
    buy, buy_quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    venue = ControlledVenue(flow, tmp_path / 'venue.json')
    transport = SDKContractTransport(risk.gate_path,
        host=Path(__file__).with_name('gmgn_execution_host_fixture.mjs'), sdk=SDK)
    original = transport.send_with_guard

    def controlled_send(contract, *, send_guard):
        reply = original(contract, send_guard=send_guard)
        assert reply['state'] == 'ACKNOWLEDGED'
        with flow.store.transaction() as c:
            event = flow.store.get(c, 'events', contract['eventId'])
        records = venue.records()
        assert event['request_id'] not in records
        venue.send_calls.append(event['side']); venue.sdk_calls.append(event['request_id'])
        record = {key: event[key] for key in ('request_id', 'event_id', 'trade_id', 'side', 'mint', 'wallet', 'payload_hash')}
        record.update(order_id=reply['order_id'], payload=json.loads(contract['payloadJson']), status='pending')
        records[event['request_id']] = record
        venue.path.write_text(canonical(records))
        return reply

    monkeypatch.setattr(transport, 'send_with_guard', controlled_send)
    adapter = SingleSendAdapter(risk, probes, contract_transport=transport)
    assert adapter.execute(buy, buy_quote)['state'] == 'ACKNOWLEDGED'
    assert flow.trade(buy)['remaining_raw'] == '0'
    venue.finalize(buy)
    assert venue.reconcile() and flow.trade(buy)['execution_state'] == 'POSITION_OPEN'
    sell = flow.store.prepare_sell(buy['trade_id'], reason='strategy_exit')
    assert sell['amount_raw'] == '1000'
    risk.gate_path.write_text(json.dumps({'schema_version': 1, 'OWNER_BROADCAST_GATE': 'OFF'}))
    flow.now = fixture_now()
    sell_quote = dict(flow.quote(sell, minimum_output_raw='50000000'), source='GMGN_ORDER_QUOTE')
    if case != 'close':
        with flow.store.transaction() as c:
            entry = flow.store.get(c, 'events', buy['event_id'])
            authorization = entry.pop('owner_entry_authorization')
            flow.store.put(c, 'events', entry['event_id'], entry)
        denied = adapter.execute(sell, sell_quote)
        assert denied['state'] == 'NO_BROADCAST' and venue.send_calls == ['BUY']
        with flow.store.transaction() as c:
            entry = flow.store.get(c, 'events', buy['event_id'])
            entry['owner_entry_authorization'] = authorization
            flow.store.put(c, 'events', entry['event_id'], entry)
        assert not flow.store.reconcile(dict(venue.reader.snapshot(flow.store), balance_lamports=str(venue.balance + 1)))
        assert adapter.execute(sell, sell_quote)['state'] == 'BLOCKED'
        assert venue.send_calls == ['BUY']
        assert production.read_bytes() == original_gate
        return
    # Fresh owner OFF is a close-only permission, never a fabricated gate ON.
    decision = adapter.assess(sell, sell_quote)
    assert decision['authorized'] and decision['mode'] == 'CLOSE_ONLY' and decision['owner_gate'] == 'OFF'
    assert adapter.execute(sell, sell_quote)['state'] == 'ACKNOWLEDGED'
    assert flow.trade(sell)['remaining_raw'] == '1000'
    venue.finalize(sell)
    observation = venue.reader.snapshot(flow.store)
    fill = next(item for item in observation['recovered_fills'] if item['data']['event_id'] == sell['event_id'])
    assert flow.store.feedback(fill['data'], fill['proof'])
    assert flow.trade(sell)['execution_state'] == 'RECONCILING'
    assert flow.trade(sell)['real_net_pnl_lamports'] is None
    assert flow.store.reconcile(observation) and venue.reconcile()
    assert flow.trade(sell)['execution_state'] == 'CLOSED'
    assert flow.trade(sell)['remaining_raw'] == '0' and venue.send_calls == ['BUY', 'SELL']
    assert len(set(venue.sdk_calls)) == len(venue.records()) == 2
    flow.now = fixture_now()
    candidate = flow.store.prepare_buy(flow.candidate())
    assert candidate is not None
    # Restore a fresh flat-wallet equity mark so unrelated safety failures
    # cannot mask the owner gate's rejection of this otherwise admissible BUY.
    flow.store.mark_equity(fixtures.observe(flow))
    result = adapter.execute(candidate, dict(flow.quote(candidate), source='GMGN_ORDER_QUOTE'))
    assert result['state'] == 'NO_BROADCAST' and not result['risk']['authorized']
    assert result['risk']['ready'] and result['risk']['failures'] == []
    assert result['risk']['owner_gate'] == 'OFF' and venue.send_calls == ['BUY', 'SELL']
    assert json.loads(risk.gate_path.read_text())['OWNER_BROADCAST_GATE'] == 'OFF'
    assert production.read_bytes() == original_gate
