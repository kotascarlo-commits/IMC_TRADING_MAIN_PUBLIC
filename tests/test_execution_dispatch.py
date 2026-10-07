"""Durable guard comparison and local handoff; isolated ledgers, no transport."""
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta, datetime, timezone

import pytest

from test_execution import flow
from test_execution_adapter import ready
from execution_adapter import SingleSendAdapter, build_swap_payload
from execution_policy import RiskEngine
from execution_state import Ledger, StateError
import execution_policy


def permit(flow, tmp_path, monkeypatch, *, pm_ttl=3):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    evidence = probes('BUY')
    # These isolated tests exercise quote/host interval encoding. Keep that
    # interval independent of the separately tested original PM lease cap.
    evidence['heartbeats']['POSITION_MANAGER']['valid_until_monotonic'] = time.monotonic() + pm_ttl
    decision = risk.assess(**evidence, event=claimed, quote=quote, lease=lease)
    return risk, risk.authorize_send(lease, decision)


@pytest.mark.parametrize('mutation', ['kill', 'pause', 'wallet', 'balance', 'api'])
def test_changed_guard_after_marker_blocks_both_outputs(flow, tmp_path, monkeypatch, mutation):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    if mutation == 'kill':
        flow.store.block('isolated_late_guard')
    elif mutation == 'pause':
        assert flow.store.control('pause_buys')
    elif mutation == 'api':
        flow.store.api_failure('isolated_failure')
    else:
        with flow.store.transaction() as c:
            flow.store.set_meta(c, 'wallet' if mutation == 'wallet' else 'wallet_balance_lamports',
                                'different-wallet' if mutation == 'wallet' else '999999999')
    with pytest.raises(StateError, match='dispatch_guard_changed'):
        risk.dispatch_contract(grant)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_payload(grant)


@pytest.mark.parametrize('first', ['payload', 'contract'])
def test_outputs_share_one_consumption_and_preserve_bytes(flow, tmp_path, monkeypatch, first):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    if first == 'payload':
        assert risk.dispatch_payload(grant) == grant.payload_json.encode()
        other = risk.dispatch_contract
    else:
        contract = risk.dispatch_contract(grant)
        assert dict(contract) == {'eventId': grant.event_id, 'requestId': grant.request_id,
            'tradeId': grant.trade_id, 'payloadJson': grant.payload_json, 'payloadHash': grant.payload_hash,
            'issuedAtMs': int(flow.now.timestamp())*1000, 'expiresAtMs': int(flow.now.timestamp())*1000+2000,
            'responseUntilMs': int(flow.now.timestamp())*1000+30000}
        assert hashlib.sha256(contract['payloadJson'].encode()).hexdigest() == contract['payloadHash']
        with pytest.raises(TypeError):
            contract['eventId'] = 'changed'
        other = risk.dispatch_payload
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        other(grant)


def test_config_is_pinned_before_approval_and_old_reference_cannot_mutate_it(flow, tmp_path, monkeypatch):
    old = flow.store.cfg
    risk, grant = permit(flow, tmp_path, monkeypatch)
    old['max_open_positions'] = 999
    assert flow.store.cfg['max_open_positions'] == 1
    with pytest.raises(TypeError):
        flow.store.cfg['max_open_positions'] = 999
    with pytest.raises(StateError, match='execution_config_pinned'):
        flow.store.cfg = old
    assert risk.dispatch_payload(grant) == grant.payload_json.encode()


def test_audit_only_activity_does_not_change_guard_snapshot(flow, tmp_path, monkeypatch):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    with flow.store.transaction() as c:
        flow.store.audit(c, 'isolated_monitoring', {'kind': 'read'})
    assert risk.dispatch_payload(grant) == grant.payload_json.encode()


def test_cloned_or_restarted_permit_cannot_dispatch(flow, tmp_path, monkeypatch):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_contract(replace(grant))
    other = RiskEngine(Ledger(flow.store.path, clock=flow.clock), gate_path=risk.gate_path, clock=flow.clock)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        other.dispatch_contract(grant)
    assert risk.dispatch_payload(grant) == grant.payload_json.encode()


def test_parallel_output_calls_consume_once(flow, tmp_path, monkeypatch):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    def run(method):
        try:
            method(grant)
            return 'issued'
        except StateError:
            return 'consumed'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [risk.dispatch_payload, risk.dispatch_contract]))
    assert sorted(results) == ['consumed', 'issued']


@pytest.mark.parametrize('phase', ['after_lock', 'before_commit', 'after_commit'])
def test_expired_final_comparison_never_issues_contract(flow, tmp_path, monkeypatch, phase):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    original = flow.store.transaction
    @contextmanager
    def delayed(**kwargs):
        if phase == 'before_commit':
            before = kwargs['before_commit']
            def waited():
                flow.now += timedelta(seconds=3)
                before()
            kwargs['before_commit'] = waited
        with original(**kwargs) as c:
            if phase == 'after_lock':
                flow.now += timedelta(seconds=3)
            yield c
        if phase == 'after_commit':
            flow.now += timedelta(seconds=3)
    monkeypatch.setattr(flow.store, 'transaction', delayed)
    with pytest.raises(StateError):
        risk.dispatch_contract(grant)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_payload(grant)


def test_real_sqlite_lock_wait_uses_remaining_permit_budget(flow, tmp_path, monkeypatch):
    risk, grant = permit(flow, tmp_path, monkeypatch)
    # The genuine permit was issued for two seconds; consume most of that
    # existing interval, never replace/extend it with a fabricated permit.
    time.sleep(1.75)
    lock = sqlite3.connect(flow.store.path, isolation_level=None)
    lock.execute('BEGIN IMMEDIATE')
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            risk.dispatch_contract(grant)
    finally:
        lock.rollback()
        lock.close()
    assert time.monotonic()-started < .8  # not the old five-second busy wait
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_payload(grant)


def test_adapter_guard_changed_after_marker_never_calls_transport(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    original = risk.authorize_send
    def changed(*args):
        grant = original(*args)
        flow.store.block('isolated_late_kill')
        return grant
    monkeypatch.setattr(risk, 'authorize_send', changed)
    calls = []
    result = SingleSendAdapter(risk, probes, transport=lambda body: calls.append(body)).execute(event, quote)
    assert result['state'] == 'UNKNOWN' and calls == []
    assert flow.store.status()['events'][0]['state'] == 'UNKNOWN'


@pytest.mark.parametrize('microseconds', [1, 999, 1001, 999999])
def test_utc_milliseconds_round_conservatively_without_renewing_time(flow, tmp_path, monkeypatch, microseconds):
    flow.now += timedelta(microseconds=microseconds)
    risk, grant = permit(flow, tmp_path, monkeypatch)
    contract = risk.dispatch_contract(grant)
    epoch_seconds = int(flow.now.timestamp())
    assert contract['issuedAtMs'] == epoch_seconds*1000+(microseconds+999)//1000
    assert contract['expiresAtMs'] == epoch_seconds*1000+2000+microseconds//1000
    assert contract['expiresAtMs']-contract['issuedAtMs'] == 1999


def test_conservative_rounding_collapsed_interval_is_rejected_once(flow, tmp_path, monkeypatch):
    monkeypatch.setattr(execution_policy.time, 'monotonic', lambda: 0.0)
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    flow.now += timedelta(seconds=1, microseconds=999999)
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    grant = risk.authorize_send(lease, decision)
    with pytest.raises(StateError, match='dispatch_host_contract_invalid'):
        risk.dispatch_contract(grant)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_payload(grant)


def test_original_interval_above_two_seconds_cannot_hide_in_rounding(flow, tmp_path, monkeypatch):
    flow.now += timedelta(microseconds=1)
    flow.store.cfg['quote_max_age_seconds'] = 2.000998
    # Isolate the host's quote interval validation from the independent PM cap.
    # The production PM lease remains two seconds.
    risk, grant = permit(flow, tmp_path, monkeypatch, pm_ttl=3)
    assert (datetime.fromisoformat(grant.valid_until)-datetime.fromisoformat(grant.issued_at)).total_seconds() == 2.000998
    with pytest.raises(StateError, match='dispatch_host_contract_invalid'):
        risk.dispatch_contract(grant)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_payload(grant)


def test_marker_commit_delay_cannot_issue_late_permit(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    original = flow.store.transaction
    @contextmanager
    def delayed(**kwargs):
        with original(**kwargs) as c:
            yield c
        flow.now += timedelta(seconds=3)
    with monkeypatch.context() as delayed_patch:
        delayed_patch.setattr(flow.store, 'transaction', delayed)
        with pytest.raises(StateError):
            risk.authorize_send(lease, decision)
    assert not risk._permits and not risk._leases
    assert flow.store.status()['events'][0]['send_started_at']


def test_marker_busy_commit_deadline_rolls_back_without_dispatch_record(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    reader = sqlite3.connect(flow.store.path, isolation_level=None)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM meta').fetchone()
    time.sleep(1.75)
    started = time.monotonic()
    try:
        with pytest.raises(StateError):
            risk.authorize_send(lease, decision)
    finally:
        reader.rollback()
        reader.close()
    assert time.monotonic()-started < .8
    assert not risk._permits and not risk._leases
    with flow.store.transaction() as c:
        assert flow.store.get(c, 'events', event['event_id'])['send_started_at'] is None
        assert c.execute("SELECT count(*) FROM audit WHERE action='single_send_started'").fetchone()[0] == 0


@pytest.mark.parametrize('scenario', ['pythonContract', 'pythonContractLateAck'])
def test_full_python_handoff_is_accepted_by_installed_sdk_boundary(flow, tmp_path, monkeypatch, scenario):
    from execution_adapter import SOL
    from test_single_swap_transport import TOKEN, run
    import test_execution_adapter as fixtures
    flow.now = datetime.now(timezone.utc)
    # Separate synthetic ledger: establish the valid-address fixture wallet
    # initially, never rebind an existing wallet anchor.
    flow.store = Ledger(tmp_path/'valid-address-fixture.db', dict(flow.store.cfg), clock=flow.clock)
    flow.store.bootstrap(flow.clock())
    assert flow.store.reconcile({'wallet': SOL, 'observed_at': flow.clock(), 'chain_positions': {},
        'executor_positions': {}, 'orders': {}, 'balance_lamports': '1000000000'})
    candidate, quote, observe = flow.candidate, flow.quote, fixtures.observe
    monkeypatch.setattr(flow, 'candidate', lambda **kw: candidate(mint=TOKEN, **kw))
    monkeypatch.setattr(flow, 'quote', lambda event, **kw: dict(quote(event, **kw), wallet=SOL))
    monkeypatch.setattr(fixtures, 'observe', lambda f: dict(observe(f), wallet=SOL))
    risk, grant = permit(flow, tmp_path, monkeypatch)
    run(scenario, json.dumps(dict(risk.dispatch_contract(grant))))


def test_response_timeout_keeps_original_claim_anchor(flow, tmp_path, monkeypatch):
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    claimed_at = flow.now
    flow.now += timedelta(seconds=1)
    decision = risk.assess(**probes('BUY'), event=claimed, quote=quote, lease=lease)
    grant = risk.authorize_send(lease, decision)
    flow.now += timedelta(milliseconds=100)
    contract = risk.dispatch_contract(grant)
    assert contract['responseUntilMs'] == int(claimed_at.timestamp())*1000+30000
    assert contract['responseUntilMs']-contract['issuedAtMs'] == 29000
    assert contract['expiresAtMs']-contract['issuedAtMs'] == 1000


@pytest.mark.parametrize('timeout', [31, 1, .0000001])
def test_response_window_invalid_never_issues_a_reusable_contract(flow, tmp_path, monkeypatch, timeout):
    flow.store.cfg['request_timeout_seconds'] = timeout
    risk, grant = permit(flow, tmp_path, monkeypatch)
    with pytest.raises(StateError, match='dispatch_response_budget_invalid'):
        risk.dispatch_contract(grant)
    with pytest.raises(StateError, match='send_permit_invalid_or_consumed'):
        risk.dispatch_payload(grant)


def test_response_end_rounds_down_without_microsecond_budget_extension(flow, tmp_path, monkeypatch):
    flow.now += timedelta(microseconds=1001)
    flow.store.cfg['request_timeout_seconds'] = 29.9999999
    risk, grant = permit(flow, tmp_path, monkeypatch)
    contract = risk.dispatch_contract(grant)
    assert contract['responseUntilMs'] == int(flow.now.timestamp())*1000+30001
    assert contract['responseUntilMs']-contract['issuedAtMs'] == 29999


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_ack_after_ledger_timeout_keeps_order_binding_and_unknown_guards(flow, tmp_path, monkeypatch, side):
    from execution_adapter import TransportReply
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch, side=side)
    before = flow.trade(event)['remaining_raw']
    with flow.store.transaction() as c:
        receipts = flow.store.all(c, 'receipts')
    calls = []
    def late(body):
        calls.append(body)
        flow.now += timedelta(seconds=31)
        assert flow.store.status()['events'][-1]['state'] == 'UNKNOWN'
        return TransportReply(200, b'{"order_id":"late-valid-order"}')
    adapter = SingleSendAdapter(risk, probes, transport=late)
    result = adapter.execute(event, quote)
    status = flow.store.status()
    stored = next(e for e in status['events'] if e['event_id'] == event['event_id'])
    assert result['state'] == stored['state'] == 'UNKNOWN'
    assert stored['order_id'] == 'late-valid-order' and stored['send_started_at']
    assert status['kill_reason'] == 'execution_timeout' and status['reconciliation_required']
    with flow.store.transaction() as c:
        assert flow.store.all(c, 'receipts') == receipts
    assert flow.trade(event)['remaining_raw'] == before
    assert adapter.execute(event, quote)['state'] == 'BLOCKED'
    assert len(calls) == 1
