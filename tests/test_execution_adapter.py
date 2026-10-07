"""Single-send fixtures only. No GMGN writes, signing, wallet or real money.

Passing 100-trade metrics below are an in-memory risk-contract fixture; they
are never written to a cohort, runtime ledger or performance report.
"""
import json
import time
import threading
from datetime import timedelta
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace

import pytest

from test_execution import flow
from test_execution_equity import observe
from execution_adapter import SingleSendAdapter, TransportReply, build_swap_payload
from execution_policy import RiskEngine, CRITICAL, CAPABILITIES
from execution_state import Ledger, StateError
import execution_policy


def ready(flow, tmp_path, monkeypatch, *, side='BUY', gate='ON'):
    if side == 'SELL':
        buy = flow.open()
        event = flow.store.prepare_sell(buy['trade_id'], reason='risk_exit')
    else:
        event = flow.buy()
    flow.store.mark_equity(observe(flow))
    original = flow.store.status

    def status(**kwargs):
        result = original(**kwargs)
        result['performance'] = {'evidence_class': 'FINALIZED_CHAIN_RECONCILED', 'source': 'isolated:contract-fixture',
                                'count': 100, 'profit_factor': '2', 'win_rate': '.70', 'top3_net_profit_share': '.25'}
        return result

    monkeypatch.setattr(flow.store, 'status', status)
    path = tmp_path / 'isolated-owner-gate.json'
    path.write_text(json.dumps({'schema_version': 1, 'OWNER_BROADCAST_GATE': gate}))
    risk = RiskEngine(flow.store, gate_path=path, clock=flow.clock)

    def probes(side):
        return {'heartbeats': {name: {'status': 'PASS', 'source': 'isolated:probe', 'observed_at': flow.clock(),
                                    'valid_until_monotonic': time.monotonic()+2}
                               for name in CRITICAL}, 'capabilities': {name: True for name in CAPABILITIES}}

    return event, dict(flow.quote(event), source='GMGN_ORDER_QUOTE'), risk, probes


def test_owner_off_builds_without_claim_or_io(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch, gate='OFF')
    calls = []
    adapter = SingleSendAdapter(risk, probes, transport=lambda body: calls.append(body))
    result = adapter.execute(event, quote)
    assert result['state'] == 'NO_BROADCAST' and not calls
    assert flow.store.status()['events'][0]['state'] == 'READY'
    payload = build_swap_payload(event, quote, flow.store.cfg)
    assert payload['auto_slippage'] is False and payload['input_amount'] == '50000000'
    assert 'percent' not in payload and 'withdraw' not in payload


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_ack_never_books_fill_and_duplicate_never_sends(flow, tmp_path, monkeypatch, side):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch, side=side)
    calls = []

    def transport(body):
        calls.append(body)
        return TransportReply(200, b'{"order_id":"isolated-order","status":"filled","output_amount":"999999999"}')

    adapter = SingleSendAdapter(risk, probes, transport=transport)
    assert adapter.execute(event, quote)['state'] == 'ACKNOWLEDGED'
    before = flow.trade(event)['remaining_raw']
    assert before == ('0' if side == 'BUY' else '1000')
    assert flow.trade(event)['state'] == side + '_ACK'
    assert adapter.execute(event, quote)['state'] == 'BLOCKED'
    assert len(calls) == 1 and isinstance(calls[0], bytes)
    stored = flow.store.status()['events'][-1]
    assert stored['request_payload_json'].encode() == calls[0]
    assert stored['send_started_at'] and stored['fee_budget_lamports'] == '3000000'


@pytest.mark.parametrize('response', [
    TransportReply(429, b'{}'), TransportReply(500, b'{}'), TransportReply(200, b''),
    TransportReply(200, b'{'), TransportReply(200, b'null'), TransportReply(200, b'{}'),
    TransportReply(200, b'{"order_id":"a","order_id":"b"}'),
    TransportReply(200, b'{"order_id":false}'), TransportReply(200, b'x' * 65537),
], ids=['429', '500', 'empty', 'invalid-json', 'null', 'no-order', 'duplicate-field', 'wrong-order-type', 'oversize'])
def test_uncertain_transport_response_is_never_retried(flow, tmp_path, monkeypatch, response):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    calls = []

    def send(body):
        calls.append(body)
        return response

    adapter = SingleSendAdapter(risk, probes, transport=send)
    assert adapter.execute(event, quote)['state'] == 'UNKNOWN'
    assert adapter.execute(event, quote)['state'] == 'BLOCKED'
    assert len(calls) == 1
    assert flow.store.status()['events'][0]['state'] == 'UNKNOWN'
    assert flow.trade(event)['remaining_raw'] == '0'


@pytest.mark.parametrize('error', [TimeoutError, ConnectionResetError, OSError])
def test_send_exception_is_sanitized_and_survives_restart(flow, tmp_path, monkeypatch, error):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    calls = []

    def send(body):
        calls.append(body)
        raise error('SECRET credential must not be persisted')

    adapter = SingleSendAdapter(risk, probes, transport=send)
    assert adapter.execute(event, quote)['state'] == 'UNKNOWN'
    restarted = Ledger(flow.store.path, clock=flow.clock)
    restarted.recover()
    other = SingleSendAdapter(RiskEngine(restarted, gate_path=risk.gate_path, clock=flow.clock), probes, transport=send)
    assert other.execute(event, quote)['state'] == 'BLOCKED' and len(calls) == 1
    with restarted.transaction() as c:
        assert not any('SECRET' in row[0] for row in c.execute('SELECT doc FROM audit'))


def test_owner_off_between_claim_and_send_consumes_claim_without_io(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    calls, count = [], [0]

    def changing(side):
        count[0] += 1
        if count[0] == 2:
            risk.gate_path.write_text('{"schema_version":1,"OWNER_BROADCAST_GATE":"OFF"}')
        return probes(side)

    adapter = SingleSendAdapter(risk, changing, transport=lambda body: calls.append(body))
    assert adapter.execute(event, quote)['state'] == 'UNKNOWN' and not calls
    assert flow.store.status()['events'][0]['state'] == 'UNKNOWN'
    assert adapter.execute(event, quote)['state'] == 'BLOCKED' and not calls


def test_ram_lease_cannot_be_copied_or_reconstructed_after_restart(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    kwargs = dict(**probes('BUY'), event=claimed, quote=quote)
    assert risk.assess(**kwargs, lease=replace(lease))['authorized'] is False
    new_risk = RiskEngine(flow.store, gate_path=risk.gate_path, clock=flow.clock)
    assert new_risk.assess(**kwargs, lease=lease)['authorized'] is False
    assert risk.assess(**kwargs, lease=lease)['authorized'] is True


def test_payload_is_frozen_and_changed_guard_cannot_be_forged(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    payload = build_swap_payload(event, quote, flow.store.cfg)
    claimed, lease = risk.reserve(event['event_id'], quote, payload)
    payload['input_amount'] = '999999999'
    assert json.loads(lease.payload_json)['input_amount'] == '50000000'
    with pytest.raises(FrozenInstanceError):
        lease.payload_json = '{}'
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    flow.store.block('isolated_guard_changed')
    with flow.store.transaction() as c:
        decision['guard_revision'] = flow.store.guard_revision(c)
    with pytest.raises(StateError, match='send_lease_not_authorized'):
        risk.authorize_send(lease, decision)
    assert flow.store.status()['events'][0]['send_started_at'] is None


def test_atomic_send_cas_rejects_changed_ledger_guard(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    assert decision['authorized']
    flow.store.block('isolated_guard_changed')
    with pytest.raises(StateError, match='send_claim_changed_or_consumed'):
        risk.authorize_send(lease, decision)
    with pytest.raises(StateError, match='send_lease_not_authorized'):
        risk.authorize_send(lease, decision)


def test_duplicate_parallel_signal_has_one_dispatch(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    calls, count, lock, barrier = [], [0], threading.Lock(), threading.Barrier(2)

    def concurrent(side):
        with lock:
            count[0] += 1
            first = count[0] <= 2
        if first:
            barrier.wait(timeout=3)
        return probes(side)

    def transport(body):
        calls.append(body)
        return TransportReply(200, b'{"order_id":"isolated-order"}')

    adapter = SingleSendAdapter(risk, concurrent, transport=transport)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(adapter.execute, event, quote) for _ in range(2)]
        results = [task.result(timeout=5)['state'] for task in tasks]
    assert sorted(results) == ['ACKNOWLEDGED', 'BLOCKED'] and len(calls) == 1


def test_one_lease_cannot_be_authorized_twice_concurrently(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)

    def authorize():
        try:
            risk.authorize_send(lease, decision)
            return 'authorized'
        except StateError:
            return 'blocked'

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: authorize(), range(2))) == ['authorized', 'blocked']


def test_known_sell_works_with_discovery_and_core_unavailable(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch, side='SELL')

    def offline_discovery(side):
        result = probes(side)
        result['heartbeats'].pop('CORE')
        result['heartbeats'].pop('MARKET_DATA')
        return result

    adapter = SingleSendAdapter(risk, offline_discovery, transport=lambda body: TransportReply(200, b'{"order_id":"known-exit"}'))
    assert adapter.execute(event, quote)['state'] == 'ACKNOWLEDGED'
    assert flow.trade(event)['remaining_raw'] == '1000'


def test_missing_production_transport_does_not_authorize(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    result = SingleSendAdapter(risk, probes).execute(event, quote)
    assert result['state'] == 'BLOCKED'
    assert 'capability:buy_transport' in result['risk']['failures']
    assert flow.store.status()['events'][0]['state'] == 'READY'


@pytest.mark.parametrize('stage', ['after-claim', 'during-send'])
def test_crash_leaves_durable_obligation_and_never_dispatches_again(flow, tmp_path, monkeypatch, stage):
    class ProcessCrash(BaseException):
        pass

    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    calls = []
    reserve = risk.reserve

    def crashing_reserve(*args):
        result = reserve(*args)
        if stage == 'after-claim':
            raise ProcessCrash()
        return result

    def send(body):
        calls.append(body)
        raise ProcessCrash()

    monkeypatch.setattr(risk, 'reserve', crashing_reserve)
    with pytest.raises(ProcessCrash):
        SingleSendAdapter(risk, probes, transport=send).execute(event, quote)
    restarted = Ledger(flow.store.path, clock=flow.clock)
    restarted.recover()
    recovery = SingleSendAdapter(RiskEngine(restarted, gate_path=risk.gate_path, clock=flow.clock), probes, transport=send)
    assert recovery.execute(event, quote)['state'] == 'BLOCKED'
    assert len(calls) == (1 if stage == 'during-send' else 0)
    assert restarted.status()['reconciliation_required']


def test_partial_order_never_qualifies_as_owned_fresh_claim(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    assert flow.store.bind_order(event['event_id'], 'order:' + event['event_id'])
    assert flow.store.feedback(*flow.fill(event, status='PARTIAL', delta=400, cash=-20000000, swap_input=20000000))
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    assert not decision['authorized']
    assert 'claim_lease_invalid' in decision['failures'] and 'unresolved_order' in decision['failures']


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_native_fee_budget_is_reserved_for_both_sides(flow, side):
    if side == 'SELL':
        buy = flow.open()
        event = flow.store.prepare_sell(buy['trade_id'])
        balance = '12999999'
    else:
        event = flow.buy()
        balance = '59999999'
        flow.store.cfg['position_fraction'] = '1'
    with flow.store.transaction() as c:
        flow.store.set_meta(c, 'wallet_balance_lamports', balance)
    quote = flow.quote(event)
    with pytest.raises(StateError, match='capital_limit|sell_fee_reserve'):
        flow.store.claim(event['event_id'], quote)
    assert flow.store.status()['events'][-1]['state'] == 'READY'


def test_risk_clock_is_refreshed_after_waiting_for_lock(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    payload = build_swap_payload(event, quote, flow.store.cfg)
    claimed, lease = risk.reserve(event['event_id'], quote, payload)
    started = threading.Event()

    def assess():
        started.set()
        return risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with risk._lock:
            task = pool.submit(assess)
            assert started.wait(1)
            flow.now += timedelta(seconds=3)
        decision = task.result(timeout=3)
    assert not decision['authorized'] and 'stale_quote' in decision['failures']


def test_expiry_is_rechecked_inside_send_cas_after_lock_wait(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    assert decision['authorized']
    original = flow.store.transaction
    waited = [False]

    @contextmanager
    def delayed(**kwargs):
        with original(**kwargs) as c:
            if not waited[0]:
                waited[0] = True
                flow.now += timedelta(seconds=3)
            yield c

    monkeypatch.setattr(flow.store, 'transaction', delayed)
    with pytest.raises(StateError):
        risk.authorize_send(lease, decision)
    assert flow.store.status()['events'][0]['send_started_at'] is None


@pytest.mark.parametrize('expiration', ['quote', 'heartbeat', 'monotonic'])
def test_permit_expiry_immediately_before_transport_blocks_io(flow, tmp_path, monkeypatch, expiration):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    mono = [0.0]
    monkeypatch.setattr(execution_policy.time, 'monotonic', lambda: mono[0])

    def evidence(side):
        result = probes(side)
        if expiration == 'heartbeat':
            result['heartbeats']['RPC']['observed_at'] = (flow.now - timedelta(seconds=14.9)).isoformat()
        return result

    original = risk.authorize_send

    def delayed(*args):
        permit = original(*args)
        if expiration == 'monotonic':
            mono[0] = 3.0
        else:
            flow.now += timedelta(seconds=3 if expiration == 'quote' else .2)
        return permit

    monkeypatch.setattr(risk, 'authorize_send', delayed)
    calls = []
    adapter = SingleSendAdapter(risk, evidence, transport=lambda body: calls.append(body))
    assert adapter.execute(event, quote)['state'] == 'UNKNOWN' and not calls
    assert flow.store.status()['events'][0]['state'] == 'UNKNOWN'


def test_approval_wait_and_wallclock_rollback_cannot_extend_monotonic_expiry(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    mono, deadlines = [0.0], []
    monkeypatch.setattr(execution_policy.time, 'monotonic', lambda: mono[0])
    original = risk._record_approval

    def waiting(*args):
        deadlines.append(args[-1])
        mono[0] = 3.0
        # After a three-second wait, wallclock rolls back inside the original
        # [issued, valid_until] window. Only the frozen monotonic bound catches it.
        flow.now += timedelta(seconds=1)
        return original(*args)

    monkeypatch.setattr(risk, '_record_approval', waiting)
    calls = []
    result = SingleSendAdapter(risk, probes, transport=lambda body: calls.append(body)).execute(event, quote)
    assert deadlines == [2.0]
    assert result['state'] == 'UNKNOWN' and not calls
    assert flow.store.status()['events'][0]['send_started_at'] is None
