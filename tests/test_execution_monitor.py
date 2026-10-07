"""Isolated read observations and atomic recovery; no broker or live transport."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

import pytest

from test_execution import flow
from execution_state import Ledger, StateError
from execution_evidence import GMGNOrderEvidence, ReconciliationReader, SOL
from execution_monitor import ReadMonitor


def observation(flow):
    return {'wallet': 'wallet', 'observed_at': flow.clock(), 'chain_positions': {},
            'executor_positions': {}, 'orders': {}, 'balance_lamports': str(flow.base)}


def test_empty_wallet_anchor_cannot_be_rebound_and_survives_restart(flow):
    tid = flow.candidate()
    assert flow.store.prepare_buy(tid) is not None
    assert flow.store.feedback(flow.data(flow.store.pending(), 'REJECTED'))
    assert all(t['wallet'] is None for t in flow.store.status()['trades'])
    assert not flow.reconcile(wallet='foreign-wallet')
    status = Ledger(flow.store.path, clock=flow.clock).status()
    assert status['wallet'] == 'wallet' and status['wallet_balance_lamports'] == str(flow.base)
    assert 'reconciliation_wallet_mismatch' in status['kill_reasons']
    assert not flow.reconcile()


def test_unknown_without_order_id_stays_blocked_after_restart_even_with_empty_wallet(flow):
    pending=flow.buy()
    event=flow.store.claim(pending['event_id'],flow.quote(pending))
    assert flow.store.feedback(dict(flow.data(event,'UNKNOWN_EXECUTION'),order_id=None))
    flow.store=Ledger(flow.store.path,clock=flow.clock)
    flow.store.recover()
    calls=[]
    class EmptyRPC:
        def wallet_snapshot(self,wallet):
            return {},{},str(flow.base),42
    def cli(args):
        calls.append(args)
        if args[:2]==['portfolio','info']:
            return {'wallets':[{'chain':'sol','address':'wallet'}]}
        if args[:2]==['portfolio','holdings']:
            return {'list':[],'next':None}
        pytest.fail('Unbound UNKNOWN must not guess an order ID or submit an order')
    reader=ReconciliationReader(flow.store.cfg,cli=cli,rpc=EmptyRPC(),clock=flow.clock)
    with pytest.raises(StateError,match='unbound_execution_obligation'):
        reader.snapshot(flow.store)
    status=flow.store.status()
    assert status['events'][0]['state']=='UNKNOWN' and status['events'][0]['order_id'] is None
    assert status['reconciliation_required'] is True
    assert status['trades'][0]['execution_state']=='BUY_UNKNOWN'
    assert not flow.store.control('reset_session')
    assert flow.store.prepare_buy(flow.candidate(mint='blocked-after-empty-wallet')) is None
    assert all(args[0]=='portfolio' for args in calls)


@pytest.mark.parametrize('anchor', [None, '', 123, [], 'x' * 129])
def test_invalid_or_lost_anchor_never_rebinds_after_history(flow, anchor):
    with flow.store.transaction() as c:
        flow.store.set_meta(c, 'wallet', anchor)
    assert not flow.reconcile()
    assert 'reconciliation_wallet_anchor_invalid' in flow.store.status()['kill_reasons']
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'wallet') == anchor


@pytest.mark.parametrize('status', ['pending', 'processed', 'failed', 'expired'])
def test_pending_observation_preserves_order_without_fill_or_api_failure(flow, status):
    event = flow.claim(flow.buy())
    class RPC:
        def wallet_snapshot(self, wallet):
            return {}, {}, str(flow.base), 42
        def fill(self, *args):
            pytest.fail('Nonfinal order must not request a fill')
    def cli(args):
        if args[:2] == ['portfolio', 'info']:
            return {'wallets': [{'chain': 'sol', 'address': 'wallet'}]}
        if args[:2] == ['portfolio', 'holdings']:
            return {'list': [], 'next': ''}
        return {'order_id': 'order:' + event['event_id'], 'status': status}
    reader = ReconciliationReader(flow.store.cfg, cli=cli, rpc=RPC(), clock=flow.clock)
    observed = reader.snapshot(flow.store)
    assert observed['recovered_fills'] == []
    assert observed['orders'][event['event_id']]['state'] == 'UNRESOLVED'
    assert not flow.store.reconcile(observed)
    assert flow.trade(event)['buy_raw'] == '0'
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'api_failures') == 0
        assert flow.store.get(c, 'events', event['event_id'])['order_id'] == 'order:' + event['event_id']


@pytest.mark.parametrize('change', [dict(status='unknown'), dict(status='pending', state=30),
    dict(state=True), dict(hash=''), dict(hash=1), dict(report={}), dict(status='confirmed')])
def test_unknown_or_contradictory_order_remains_error(flow, change):
    event = flow.claim(flow.buy())
    event['order_id'] = 'order:' + event['event_id']
    order = dict(order_id=event['order_id'], status='pending')
    order.update(change)
    with pytest.raises(StateError):
        GMGNOrderEvidence(flow.store.cfg).observe(event, order)


def test_documented_sdk_order_cannot_override_conflicting_or_missing_evidence(flow):
    event = flow.claim(flow.buy())
    event['order_id'] = 'order:' + event['event_id']
    response = {'order_id': event['order_id'], 'hash': 'b' * 88, 'state': 30,
                'confirmation': {'state': 'confirmed'}, 'chain': 'sol', 'from_address': 'wallet',
                'input_token': SOL, 'output_token': 'mint',
                'filled_input_amount': '50000000', 'filled_output_amount': '1000'}
    reader = GMGNOrderEvidence(flow.store.cfg)
    assert reader.observe(event, response)['swap_output_raw'] == '1000'
    for change in (dict(status='failed'), dict(filled_output_amount=''),
                   dict(from_address='foreign'), dict(error_code='FAILURE'),
                   dict(report={'input_token': SOL, 'output_token': 'mint',
                                'input_amount': '50000000', 'output_amount': '999'}),
                   dict(state=None)):
        with pytest.raises(StateError):
            reader.observe(event, dict(response, **change))
    pending = dict(response, state=10, confirmation={'state': 'failed'}, error_code='FAILURE')
    assert reader.observe(event, pending)['state'] == 'UNRESOLVED'
    assert flow.trade(event)['buy_raw'] == '0'


def test_wallet_guard_precedes_recovery_and_keeps_existing_receipts(flow):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    observed = observation(flow)
    observed.update(wallet='foreign-wallet', recovered_fills=[{'data': data, 'proof': proof}])
    assert not flow.store.reconcile(observed)
    assert flow.trade(event)['buy_raw'] == '0'
    with flow.store.transaction() as c:
        assert flow.store.all(c, 'receipts') == []
        assert c.execute('SELECT count(*) FROM feedback').fetchone()[0] == 0


def test_freshness_rechecked_after_sqlite_lock_acquisition(flow, monkeypatch):
    observed = observation(flow)
    original = flow.store.transaction
    @contextmanager
    def delayed(**kwargs):
        with original(**kwargs) as c:
            flow.now += timedelta(seconds=16)
            yield c
    monkeypatch.setattr(flow.store, 'transaction', delayed)
    with pytest.raises(StateError, match='stale_reconciliation'):
        flow.store.reconcile(observed)
    monkeypatch.setattr(flow.store, 'transaction', original)
    with original() as c:
        assert flow.store.meta(c, 'reconciliation_generation') == 1


def test_late_application_rolls_back_receipt_and_cash(flow, monkeypatch):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    observed = observation(flow)
    observed.update(chain_positions={'mint': '1000'}, executor_positions={'mint': '1000'},
        balance_lamports='950000000', orders={event['event_id']: {'state': 'FILLED', 'order_id': data['order_id']}},
        recovered_fills=[{'data': data, 'proof': proof}])
    original = flow.store._feedback
    def late(*args):
        result = original(*args)
        flow.now += timedelta(seconds=16)
        return result
    monkeypatch.setattr(flow.store, '_feedback', late)
    with pytest.raises(StateError, match='stale_reconciliation'):
        flow.store.reconcile(observed)
    assert flow.trade(event)['buy_raw'] == '0'
    with flow.store.transaction() as c:
        assert flow.store.all(c, 'receipts') == []


def test_monitor_reconciles_and_marks_native_cash_without_a_candidate(flow, tmp_path):
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs): return observation(flow)
    monitor = ReadMonitor(flow.store, tmp_path / 'monitor.json', reader_factory=Reader)
    assert monitor.tick()
    projected = json.loads(monitor.status_path.read_text())
    assert projected['read_state'] == 'VERIFIED'
    assert projected['equity_state'] == 'VERIFIED_NATIVE_ONLY'
    assert projected['live_ready'] is False and projected['broadcast'] is False
    assert flow.store.status()['equity']['current_lamports'] == str(flow.base)


def test_monitor_http_and_periodic_ticks_do_not_overlap(flow, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs):
            calls.append(1); entered.set()
            assert release.wait(2)
            return observation(flow)
    monitor = ReadMonitor(flow.store, tmp_path / 'monitor.json', reader_factory=Reader)
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(monitor.tick()))
    worker.start()
    try:
        assert entered.wait(2)
        with pytest.raises(StateError, match='reconciliation_busy'):
            monitor.tick()
        assert len(calls) == 1
    finally:
        release.set(); worker.join(2)
    assert outcomes == [True] and not worker.is_alive()


def test_monitor_error_redaction_and_persistent_failure_limit(flow, tmp_path):
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs): raise TimeoutError('SECRET credential https://secret')
    monitor = ReadMonitor(flow.store, tmp_path / 'monitor.json', reader_factory=Reader)
    for _ in range(3):
        with pytest.raises(TimeoutError): monitor.tick()
    body = monitor.status_path.read_text()
    assert 'SECRET' not in body and 'https' not in body
    assert json.loads(body)['read_state'] == 'UNKNOWN'
    assert 'api_failure_limit' in Ledger(flow.store.path, clock=flow.clock).status()['kill_reasons']


def test_monitor_does_not_invent_open_position_marks(flow, tmp_path):
    flow.open()
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs):
            status = store.status(); observed = observation(flow)
            observed.update(chain_positions={'mint': '1000'}, executor_positions={'mint': '1000'},
                balance_lamports='950000000', orders={e['event_id']: {'state': 'FILLED', 'order_id': e['order_id']}
                                                    for e in status['events']})
            return observed
    monitor = ReadMonitor(flow.store, tmp_path / 'monitor.json', reader_factory=Reader)
    assert monitor.tick()
    assert flow.store.status()['equity'] is None
    assert json.loads(monitor.status_path.read_text())['equity_state'] == 'UNKNOWN'


@pytest.mark.parametrize('callback_first', [True, False])
def test_callback_and_recovery_share_identical_receipt_without_double_cash(flow, callback_first):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    if callback_first:
        assert flow.store.feedback(data, proof)
    class RPC:
        def wallet_snapshot(self, wallet):
            return {'mint': '1000'}, {'mint': 3}, '950000000', 42
        def fill(self, ev, signature): return dict(proof)
    def cli(args):
        if args[:2] == ['portfolio', 'info']:
            return {'wallets': [{'chain': 'sol', 'address': 'wallet'}]}
        if args[:2] == ['portfolio', 'holdings']:
            return {'list': [{'token': {'token_address': 'mint'}, 'balance': '1'}], 'next': ''}
        return {'order_id': data['order_id'], 'hash': data['signature'], 'status': 'confirmed', 'state': 30,
                'report': {'input_token': SOL, 'output_token': 'mint', 'input_amount': '50000000', 'output_amount': '1000'}}
    observed = ReconciliationReader(flow.store.cfg, cli=cli, rpc=RPC(), clock=flow.clock).snapshot(flow.store)
    assert 'state' not in observed['recovered_fills'][0]['proof']
    assert flow.store.reconcile(observed)
    with flow.store.transaction() as c:
        receipts = flow.store.all(c, 'receipts')
    assert flow.store.feedback(data, proof)
    with flow.store.transaction() as c:
        assert flow.store.all(c, 'receipts') == receipts
    assert flow.trade(event)['wallet_delta_lamports'] == '-50000000'
    assert flow.trade(event)['buy_raw'] == '1000'
    assert flow.store.status()['kill_reason'] is None


def test_failed_observation_never_expires_overdue_sent_event(flow):
    event = flow.claim(flow.buy())
    flow.now += timedelta(seconds=31)
    def saved():
        with sqlite3.connect(flow.store.path) as c:
            return {table: c.execute('SELECT * FROM ' + table).fetchall()
                    for table in ('meta', 'trades', 'events', 'receipts', 'feedback', 'audit')}
    before = saved()
    class RPC:
        def wallet_snapshot(self, wallet): return {}, {}, str(flow.base), 42
    def cli(args):
        if args[:2] == ['portfolio', 'info']:
            return {'wallets': [{'chain': 'sol', 'address': 'wallet'}]}
        if args[:2] == ['portfolio', 'holdings']: return {'list': [], 'next': ''}
        raise TimeoutError('isolated order read failure')
    with pytest.raises(TimeoutError):
        ReconciliationReader(flow.store.cfg, cli=cli, rpc=RPC(), clock=flow.clock).snapshot(flow.store)
    assert saved() == before


def test_late_early_return_rolls_back_recovery_on_inventory_mismatch(flow, monkeypatch):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    observed = observation(flow)
    observed['recovered_fills'] = [{'data': data, 'proof': proof}]
    original = flow.store.halt
    def delayed(c, reason):
        if reason == 'position_balance_disagreement': flow.now += timedelta(seconds=16)
        return original(c, reason)
    monkeypatch.setattr(flow.store, 'halt', delayed)
    with pytest.raises(StateError, match='stale_reconciliation'):
        flow.store.reconcile(observed)
    assert flow.trade(event)['buy_raw'] == '0'
    with flow.store.transaction() as c:
        assert flow.store.all(c, 'receipts') == []


def test_contention_does_not_reopen_error_or_status_wait_budget(flow, tmp_path):
    flow.store.cfg['reconciliation_max_age_seconds'] = .15
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs): return observation(flow)
    monitor = ReadMonitor(flow.store, tmp_path / 'monitor.json', reader_factory=Reader)
    writer = sqlite3.connect(flow.store.path, isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError): monitor.tick()
        assert time.monotonic() - started < 1
        assert len(monitor._pending_errors) == 1
    finally:
        writer.rollback(); writer.close()
    assert monitor.tick()
    assert not monitor._pending_errors
    with flow.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM audit WHERE action='api_failure'").fetchone()[0] == 1


@pytest.mark.parametrize('release_before_deadline', [False, True])
def test_shared_reader_commit_wait_rechecks_deadline_without_reapplying_fill(flow, release_before_deadline):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    observed = observation(flow)
    observed.update(chain_positions={'mint': '1000'}, executor_positions={'mint': '1000'},
        balance_lamports='950000000', orders={event['event_id']: {'state': 'FILLED', 'order_id': data['order_id']}},
        recovered_fills=[{'data': data, 'proof': proof}])
    def saved(c):
        return {table: c.execute('SELECT * FROM ' + table).fetchall()
                for table in ('meta', 'trades', 'events', 'receipts', 'feedback', 'audit')}
    reader = sqlite3.connect(flow.store.path, isolation_level=None)
    reader.execute('BEGIN')
    before = saved(reader)  # acquire and hold the shared rollback-journal lock
    deadline = time.monotonic() + .35
    outcomes = []
    def apply():
        try: outcomes.append(flow.store.reconcile(observed, deadline=deadline))
        except Exception as error: outcomes.append(error)
    worker = threading.Thread(target=apply)
    worker.start()
    try:
        if release_before_deadline:
            time.sleep(.05)
        else:
            worker.join(1)
            assert not worker.is_alive(), 'commit reopened an internal SQLite wait'
        reader.rollback(); reader.close()
        worker.join(1)
        assert not worker.is_alive()
        if release_before_deadline:
            assert outcomes == [True]
            assert flow.trade(event)['wallet_delta_lamports'] == '-50000000'
            with flow.store.transaction() as c:
                assert len(flow.store.all(c, 'receipts')) == 1
        else:
            assert len(outcomes) == 1 and isinstance(outcomes[0], StateError)
            with sqlite3.connect(flow.store.path) as c:
                assert saved(c) == before
    finally:
        try: reader.close()
        except sqlite3.Error: pass
        worker.join(1)


def monitor_for(flow, path, snapshot=None):
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs):
            return snapshot() if snapshot is not None else observation(flow)
    return ReadMonitor(flow.store, path, reader_factory=Reader)


def test_session_loss_keeps_coherent_wallet_and_native_equity_monitoring(flow, tmp_path):
    buy = flow.open()
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell, cash=1_000_000))
    assert flow.reconcile() is False
    assert flow.trade(sell)['state'] == 'CLOSED'
    def snapshot():
        observed = observation(flow)
        observed['balance_lamports'] = '951000000'
        return observed
    monitor = monitor_for(flow, tmp_path / 'monitor.json', snapshot)
    for _ in range(2):
        assert monitor.tick() is False  # risk permission remains blocked
        status = flow.store.status()
        assert status['kill_reason'] == 'session_loss_limit'
        assert status['reconciliation_required'] is False
        assert status['new_buys_enabled'] is False
        assert status['equity']['fresh'] and status['equity']['current_lamports'] == '951000000'
        assert monitor._state['read_state'] == 'VERIFIED'
        assert monitor._state['equity_state'] == 'VERIFIED_NATIVE_ONLY'
    assert Ledger(flow.store.path, clock=flow.clock).status()['kill_reason'] == 'session_loss_limit'


def test_native_reads_continue_with_permanent_drawdown_kill(flow, tmp_path):
    flow.store.block('max_drawdown_15pct')
    monitor = monitor_for(flow, tmp_path / 'monitor.json')
    assert monitor.tick() is False
    status = flow.store.status()
    assert status['new_buys_enabled'] is False and status['equity']['fresh']
    assert monitor._state['equity_state'] == 'VERIFIED_NATIVE_ONLY'
    assert status['kill_reason'] == 'max_drawdown_15pct'


@pytest.mark.parametrize('reason', ['session_loss_limit', 'max_drawdown_15pct'])
def test_financial_halt_never_clears_an_existing_reconciliation_fault(flow, reason):
    with flow.store.transaction() as c:
        flow.store.set_meta(c, 'reconciliation_required', True)
        flow.store.halt(c, reason)
        assert flow.store.meta(c, 'reconciliation_required') is True


@pytest.mark.parametrize('change', [dict(wallet='foreign'), dict(chain_positions={'unknown': '1'}),
                                   dict(balance_lamports='1000000001')])
def test_conflicting_read_never_produces_coherent_native_equity(flow, tmp_path, change):
    def snapshot():
        result = observation(flow)
        result.update(change)
        return result
    monitor = monitor_for(flow, tmp_path / 'monitor.json', snapshot)
    assert monitor.tick() is False
    assert monitor._state['read_state'] == 'UNRESOLVED'
    assert monitor._state['equity_state'] == 'UNKNOWN'
    assert flow.store.status()['equity'] is None


def test_unknown_order_prevents_monitor_coherence_even_with_unchanged_wallet(flow, tmp_path):
    flow.claim(flow.buy())
    monitor = monitor_for(flow, tmp_path / 'monitor.json')
    assert monitor.tick() is False
    assert monitor._state['read_state'] == 'UNRESOLVED'
    assert flow.store.status()['equity'] is None


@pytest.mark.parametrize('key,value', [('wallet', 'foreign'), ('wallet_balance_lamports', '2'),
    ('reconciliation_generation', 2), ('reconciliation_revision', 'foreign'),
    ('reconciled_at', '2000-01-01T00:00:00+00:00')])
def test_matcher_rejects_metadata_detached_from_current_audit(flow, key, value):
    observed = observation(flow)
    with flow.store.transaction() as c:
        assert flow.store.reconciliation_matches(c, observed)
        flow.store.set_meta(c, key, value)
        assert not flow.store.reconciliation_matches(c, observed)


def test_matcher_rejects_newer_foreign_audit_and_missing_audit(flow):
    observed = observation(flow)
    with flow.store.transaction() as c:
        record = json.loads(c.execute("SELECT doc FROM audit WHERE action='reconciliation' ORDER BY id DESC LIMIT 1").fetchone()[0])
        record['observation']['wallet'] = 'foreign'
        flow.store.audit(c, 'reconciliation', record)
        assert not flow.store.reconciliation_matches(c, observed)


def test_matcher_rejects_duplicated_current_revision(flow):
    observed = observation(flow)
    with flow.store.transaction() as c:
        record = json.loads(c.execute("SELECT doc FROM audit WHERE action='reconciliation' ORDER BY id DESC LIMIT 1").fetchone()[0])
        flow.store.audit(c, 'reconciliation', record)
        assert not flow.store.reconciliation_matches(c, observed)
        c.execute("DELETE FROM audit WHERE action='reconciliation'")
        assert not flow.store.reconciliation_matches(c, observed)


def test_native_mark_uses_original_read_time_without_extending_freshness(flow, tmp_path):
    original = observation(flow)
    def snapshot():
        flow.now += timedelta(seconds=10)
        return original
    monitor = monitor_for(flow, tmp_path / 'monitor.json', snapshot)
    assert monitor.tick()
    assert flow.store.status()['equity']['observed_at'] == original['observed_at']
    flow.now += timedelta(seconds=6)
    assert flow.store.status()['equity']['fresh'] is False


def test_revision_change_between_match_and_mark_never_rebinds_old_observation(flow, tmp_path, monkeypatch):
    original = flow.store.mark_equity
    def changed(observed, **kwargs):
        flow.now += timedelta(seconds=1)
        assert flow.reconcile()
        return original(observed, **kwargs)
    monkeypatch.setattr(flow.store, 'mark_equity', changed)
    monitor = monitor_for(flow, tmp_path / 'monitor.json')
    with pytest.raises(StateError, match='equity_inventory_revision_mismatch'):
        monitor.tick()
    assert flow.store.status()['equity'] is None
    assert monitor._state['read_state'] == 'UNKNOWN'


def test_clock_rollback_during_read_aborts_before_reconciliation_commit(flow, tmp_path):
    initial = observation(flow)
    flow.now += timedelta(seconds=5)
    def snapshot():
        flow.now -= timedelta(seconds=1)  # still newer than the observation
        return initial
    monitor = monitor_for(flow, tmp_path / 'monitor.json', snapshot)
    with pytest.raises(StateError, match='invalid_timestamp'):
        monitor.tick()
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'reconciliation_generation') == 1
    assert flow.store.status()['equity'] is None


def wait_monitor_pass(monitor):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        if monitor.heartbeat()['status'] == 'PASS': return
        time.sleep(.01)
    pytest.fail('owned read monitor did not become healthy')


def test_owned_heartbeat_rejects_missing_dead_busy_stopped_or_expired_worker(flow, tmp_path):
    flow.store.cfg['reconciliation_poll_seconds'] = 1000
    monitor = monitor_for(flow, tmp_path / 'monitor.json')
    assert monitor.tick()
    assert monitor.heartbeat()['status'] == 'UNKNOWN'
    monitor.start()
    try:
        wait_monitor_pass(monitor)
        monitor._lock.acquire()
        try: assert monitor.heartbeat()['status'] == 'UNKNOWN'
        finally: monitor._lock.release()
        monitor.monotonic = lambda: time.monotonic() + 20
        assert monitor.heartbeat()['status'] == 'UNKNOWN'
        monitor.monotonic = time.monotonic
        assert monitor.heartbeat()['status'] == 'UNKNOWN'  # expired lease stays invalid
    finally:
        monitor.stop()
    assert not monitor._thread.is_alive() and monitor.heartbeat()['status'] == 'UNKNOWN'
    with pytest.raises(StateError, match='monitor_stopped'): monitor.tick()
    with pytest.raises(StateError): monitor.start()


def test_heartbeat_detects_rollback_above_observation_without_renewing_deadline(flow, tmp_path):
    flow.store.cfg['reconciliation_poll_seconds'] = 1000
    monitor = monitor_for(flow, tmp_path / 'monitor.json')
    monitor.start()
    try:
        wait_monitor_pass(monitor)
        original_deadline = monitor._lease[0]
        flow.now += timedelta(seconds=5)
        assert monitor.heartbeat()['status'] == 'PASS'
        assert monitor._lease[0] == original_deadline
        flow.now -= timedelta(seconds=1)
        assert monitor.heartbeat()['status'] == 'UNKNOWN'
        flow.now += timedelta(seconds=1)
        assert monitor.heartbeat()['status'] == 'UNKNOWN'
    finally:
        monitor.stop()


def test_http_tick_stop_timeout_is_visible_and_late_completion_cannot_restore_pass(flow, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def snapshot():
        entered.set()
        assert release.wait(2)
        return observation(flow)
    monitor = monitor_for(flow, tmp_path / 'monitor.json', snapshot)
    monitor.STOP_WAIT_SECONDS = .03
    outcomes = []
    def run():
        try: monitor.tick()
        except Exception as error: outcomes.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        with pytest.raises(StateError, match='monitor_shutdown_timeout'): monitor.stop()
        assert time.monotonic() - started < .5
        assert monitor.heartbeat()['status'] == 'UNKNOWN'
        with pytest.raises(StateError, match='monitor_stopped'): monitor.tick()
    finally:
        release.set(); worker.join(2)
    assert not worker.is_alive() and len(outcomes) == 1
    assert str(outcomes[0]) == 'monitor_stopped'
    assert monitor._state['last_error'] == 'ReadMonitorShutdownTimeout'
    assert json.loads(monitor.status_path.read_text())['read_state'] == 'UNKNOWN'
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'reconciliation_generation') == 1
        assert flow.store.meta(c, 'api_failures') == 0


def test_gateway_closes_server_even_when_both_owned_loops_fail_to_stop(flow, monkeypatch):
    import imc_pure_signal_gateway as gateway
    calls = []
    class Loop:
        def start(self): pass
        def stop(self):
            calls.append('stop')
            raise StateError('shutdown_timeout')
    class Server:
        def __init__(self, *args): pass
        def serve_forever(self): raise KeyboardInterrupt()
        def server_close(self): calls.append('close')
    monkeypatch.setattr(gateway, 'ledger', lambda: flow.store)
    monkeypatch.setattr(gateway, 'write_status', lambda store: None)
    monkeypatch.setattr(gateway, 'read_monitor', Loop)
    monkeypatch.setattr(gateway, 'position_manager', Loop)
    monkeypatch.setattr(gateway, 'ThreadingHTTPServer', Server)
    with pytest.raises(StateError, match='shutdown_timeout'): gateway.main()
    assert calls == ['stop', 'stop', 'close']


@pytest.mark.parametrize('changed', [1.0, True])
def test_matcher_rejects_numeric_type_change_in_persisted_observation(flow, changed):
    observed = observation(flow)
    observed['chain_slot'] = 1
    assert flow.store.reconcile(observed)
    with flow.store.transaction() as c:
        assert flow.store.reconciliation_matches(c, observed)
        row = c.execute("SELECT id,doc FROM audit WHERE action='reconciliation' ORDER BY id DESC LIMIT 1").fetchone()
        record = json.loads(row['doc'])
        record['observation']['chain_slot'] = changed
        c.execute('UPDATE audit SET doc=? WHERE id=?', (json.dumps(record), row['id']))
        assert not flow.store.reconciliation_matches(c, observed)
