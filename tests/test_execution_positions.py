"""Isolated durable exit decisions, no provider, production port or transport."""
import json
import sqlite3
import time
import threading
from contextlib import contextmanager
from datetime import timedelta

import pytest

from test_execution import flow
from test_execution_equity import observe
from execution_positions import PositionManager, POLICY_HASH
from execution_state import Ledger, LedgerConnection, StateError, load_config
from execution_policy import RiskEngine
import imc_pure_signal_gateway as gateway


def intent(flow, event):
    with flow.store.transaction() as c:
        return flow.store.meta(c, 'position_exit:' + event['trade_id'])


def mark(flow, value, costs='0'):
    return flow.store.mark_equity(observe(flow, value=str(value), costs=str(costs)))


def test_frozen_policy_precedes_every_send_and_first_mark(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    state = manager.tick()
    assert state['status'] == 'PASS' and state['valuation_state'] == 'UNKNOWN'
    assert state['positions'] == 1 and state['broadcast'] is False
    with flow.store.transaction() as c:
        tracking = flow.store.meta(c, 'position_track:' + buy['trade_id'])
        assert tracking['policy_hash'] == POLICY_HASH
        assert tracking['mark_count'] == 0 and tracking['peak_lamports'] is None
        rows = list(c.execute('SELECT action,doc FROM audit ORDER BY id'))
        actions = [r[0] for r in rows]
        claim = next(i for i, r in enumerate(rows) if r[0] == 'transition' and
                     json.loads(r[1]).get('reason') == 'executor_claim_before_external_io')
        assert actions.index('position_policy') < actions.index('position_binding') < claim
    mark(flow, 50_000_000)
    manager.tick()
    with flow.store.transaction() as c:
        tracking = flow.store.meta(c, 'position_track:' + buy['trade_id'])
        assert tracking['mark_count'] == 1 and tracking['peak_lamports'] == '50000000'
    assert intent(flow, buy) is None


@pytest.mark.parametrize('value,reason', [(43_999_999,'stop_loss'), (44_000_000,'stop_loss'),
    (44_000_001,None), (59_999_999,None), (60_000_000,'take_profit'), (60_000_001,'take_profit')])
def test_exact_net_stop_and_profit_thresholds(flow, value, reason):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, value)
    manager.tick()
    saved = intent(flow, buy)
    assert (saved['reason'] if saved else None) == reason
    if reason:
        assert flow.store.pending()['amount_raw'] == '1000'


def test_future_costs_above_output_are_signed_without_charging_entry_fees_again(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    equity = mark(flow, 10_000_000, 11_000_000)
    assert equity['current_lamports'] == '949000000'
    manager.tick()
    assert intent(flow, buy)['reason'] == 'stop_loss'
    with flow.store.transaction() as c:
        row = json.loads(c.execute("SELECT doc FROM audit WHERE action='position_mark'").fetchone()[0])
        assert row['net_pnl_lamports'] == '-51000000'
        assert row['return_lamports'] == '-1000000'


@pytest.mark.parametrize('value,triggered', [(51_700_001,False), (51_700_000,True), (51_699_999,True)])
def test_trailing_peak_survives_restart_and_exact_six_percent_boundary(flow, value, triggered):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 55_000_000)  # arm at exactly ten percent, below TP
    manager.tick()
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    manager = PositionManager(flow.store)
    assert intent(flow, buy) is None  # normal open-position restart is neutral
    assert flow.reconcile()
    mark(flow, value)
    manager.tick()
    saved = intent(flow, buy)
    assert (saved is not None) is triggered
    if triggered:
        assert saved['reason'] == 'trailing_stop'
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_track:' + buy['trade_id'])['peak_lamports'] == '55000000'


def test_hold_uses_finalized_buy_time_and_intent_survives_stale_inventory(flow):
    buy = flow.claim(flow.buy(signal_at=(flow.now-timedelta(seconds=2)).isoformat()))
    assert flow.store.feedback(*flow.fill(buy))
    assert flow.reconcile()
    manager = PositionManager(flow.store)
    flow.now += timedelta(seconds=178)
    manager.tick()
    assert intent(flow, buy) is None
    flow.now += timedelta(seconds=2)
    manager.tick()
    saved = intent(flow, buy)
    assert saved['reason'] == 'maximum_hold'
    assert flow.store.pending() is None  # no fresh reconciliation, no SELL
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    assert flow.reconcile()
    assert intent(flow, buy) == saved
    assert flow.store.pending()['exit_reason'] == 'maximum_hold'


@pytest.mark.parametrize('mutation', ['stale_quote','revision','quantity','wallet','future_quote'])
def test_price_decisions_never_use_unbound_or_stale_marks(flow, mutation):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 1)
    if mutation == 'stale_quote':
        flow.now += timedelta(seconds=3)
        manager.tick()
    elif mutation == 'revision':
        assert flow.reconcile()
        manager.tick()
    else:
        with flow.store.transaction() as c:
            equity = flow.store.meta(c, 'equity')
            entry = equity['marks'][buy['trade_id']]
            if mutation == 'quantity': entry['remaining_raw'] = '999'
            elif mutation == 'wallet': entry['wallet'] = 'foreign'
            else: entry['quoted_at'] = (flow.now+timedelta(seconds=1)).isoformat()
            flow.store.set_meta(c, 'equity', equity)
        with pytest.raises(StateError):
            manager.tick()
        assert manager.state['status'] == 'UNKNOWN'
    assert intent(flow, buy) is None


@pytest.mark.parametrize('mutation', ['missing_tracking','peak','count','last_mark','missing_policy'])
def test_lost_state_is_never_initialized_on_restart(flow, mutation):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 55_000_000)
    manager.tick()
    with flow.store.transaction() as c:
        key = 'position_track:' + buy['trade_id']
        if mutation == 'missing_tracking': c.execute('DELETE FROM meta WHERE key=?', (key,))
        elif mutation == 'missing_policy': c.execute("DELETE FROM meta WHERE key='position_policy'")
        else:
            tracking = flow.store.meta(c, key)
            if mutation == 'peak': tracking['peak_lamports'] = None
            elif mutation == 'count': tracking['mark_count'] = 0
            else: tracking['last_mark'] = None
            flow.store.set_meta(c, key, tracking)
    if mutation == 'missing_policy':
        with pytest.raises(StateError, match='policy_lost'):
            PositionManager(flow.store)
    else:
        restarted = PositionManager(flow.store)
        with pytest.raises(StateError):
            restarted.tick()
        assert restarted.state['status'] == 'UNKNOWN'


def test_policy_missing_before_claim_blocks_the_send_marker(flow):
    event = flow.buy()
    with flow.store.transaction() as c:
        c.execute("DELETE FROM meta WHERE key='position_policy'")
    with pytest.raises(StateError, match='position_policy_unknown'):
        flow.claim(event)
    with flow.store.transaction() as c:
        current = flow.store.get(c, 'events', event['event_id'])
        assert current['state'] == 'READY' and current.get('sent_at') is None
        assert flow.store.meta(c, 'position_track:' + event['trade_id']) is None


def test_unknown_buy_cannot_receive_a_retroactive_policy(flow):
    flow.claim(flow.buy())
    with flow.store.transaction() as c:
        c.execute("DELETE FROM meta WHERE key='position_policy'")
        c.execute("DELETE FROM audit WHERE action='position_policy'")
    with pytest.raises(StateError, match='requires_unexecuted_ledger'):
        PositionManager(flow.store)


def test_global_risk_trigger_is_cas_neutral_and_survives_kill_clear(flow):
    flow.store.cfg['max_open_positions'] = 4
    buy = flow.open()
    manager = PositionManager(flow.store)
    before = flow.trade(buy)
    flow.store.block('api_failure_limit')
    after = flow.trade(buy)
    assert after['revision'] == before['revision'] and not after['exit_requested']
    assert flow.reconcile()  # transient kill clears; durable risk exit remains
    assert flow.store.status()['kill_reason'] is None
    assert flow.store.prepare_buy(flow.candidate(mint='other')) is None
    manager.tick()
    assert intent(flow, buy)['reason'] == 'risk_exit'
    assert flow.store.pending()['side'] == 'SELL'


def two_positions(flow):
    flow.store.cfg['max_open_positions'] = 4
    first = flow.open()
    second = flow.claim(flow.buy(mint='other'))
    assert flow.store.feedback(*flow.fill(second, status='PARTIAL', delta=500, cash=-25_000_000,
                                        swap_input=25_000_000, signature='c'*88))
    return first, second


def test_recovery_and_unresolved_reconciliation_do_not_liquidate_other_position(flow):
    first, second = two_positions(flow)
    manager = PositionManager(flow.store)
    flow.store.recover()
    assert not flow.reconcile()
    manager.tick()
    assert intent(flow, first) is None
    assert intent(flow, second)['reason'] == 'recovery_exit'
    assert flow.store.feedback(*flow.fill(second, delta=500, cash=-25_000_000,
        swap_input=25_000_000, signature='d'*88, suffix=':final'))
    assert flow.reconcile()
    manager.tick()
    assert intent(flow, first) is None
    assert intent(flow, second)['reason'] == 'recovery_exit'
    assert flow.trade(second)['remaining_raw'] == '1000'
    assert len([e for e in flow.store.status()['events'] if e['side'] == 'SELL']) == 1


def test_new_global_risk_still_affects_both_positions_during_recovery(flow):
    first, second = two_positions(flow)
    manager = PositionManager(flow.store)
    flow.store.recover()
    flow.store.block('max_drawdown_15pct')
    manager.tick()
    assert intent(flow, first)['reason'] == intent(flow, second)['reason'] == 'risk_exit'
    assert flow.store.pending() is None  # never send against unresolved B


def test_pause_is_not_liquidation_and_session_exit_is_explicit(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    assert flow.store.control('pause_buys')
    manager.tick()
    assert intent(flow, buy) is None
    assert flow.store.control('session_exit')
    assert intent(flow, buy)['reason'] == 'session_exit'
    assert not flow.store.status()['new_buys_enabled']


@pytest.mark.parametrize('database', ['missing','invalid','locked'])
def test_gateway_starts_and_exits_before_any_discovery_access(flow, monkeypatch, tmp_path, database):
    buy = flow.open()
    flow.now += timedelta(seconds=180)
    assert flow.reconcile()
    path = tmp_path / 'discovery.db'
    reader = None
    if database == 'invalid': path.write_bytes(b'not a database')
    elif database == 'locked':
        reader = sqlite3.connect(path)
        reader.execute('CREATE TABLE picks(bogus TEXT)')
        reader.execute('BEGIN EXCLUSIVE')
    monkeypatch.setattr(gateway, '_LEDGER', None)
    monkeypatch.setattr(gateway, '_POSITIONS', None)
    monkeypatch.setattr(gateway, 'LEDGER_PATH', flow.store.path)
    monkeypatch.setattr(gateway, 'STATE', tmp_path / 'absent-legacy.json')
    monkeypatch.setattr(gateway, 'DB', path)
    def forbidden():
        raise AssertionError('Discovery must not be read before position reconstruction/exit')
    monkeypatch.setattr(gateway, 'latest_decision_at', forbidden)
    monkeypatch.setattr(gateway, 'conn', forbidden)
    try:
        store = gateway.ledger()
        store.clock = flow.clock
        manager = gateway.position_manager()
        event = gateway.next_event()
        assert event['side'] == 'SELL' and event['trade_id'] == buy['trade_id']
        assert event['amount_raw'] == '1000' and event['exit_reason'] == 'maximum_hold'
        assert manager.state['positions'] == 1
    finally:
        if reader is not None: reader.rollback(); reader.close()


def test_busy_position_tick_never_falls_back_to_buy(flow, monkeypatch, tmp_path):
    event = flow.buy()
    manager = PositionManager(flow.store)
    monkeypatch.setattr(gateway, '_LEDGER', flow.store)
    monkeypatch.setattr(gateway, '_POSITIONS', manager)
    monkeypatch.setattr(gateway, 'DB', tmp_path / 'absent.db')
    manager._lock.acquire()
    try:
        with pytest.raises(StateError, match='position_manager_busy'):
            gateway.next_event()
    finally:
        manager._lock.release()
    assert flow.store.pending() == event


def test_expired_price_commit_rolls_back_peak_and_intent(flow, monkeypatch):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 1)
    original = flow.store.set_meta
    def delayed(c, key, value):
        result = original(c, key, value)
        if key == 'position_manager_at': flow.now += timedelta(seconds=3)
        return result
    monkeypatch.setattr(flow.store, 'set_meta', delayed)
    with pytest.raises(StateError, match='position_mark_changed_or_expired'):
        manager.tick()
    assert intent(flow, buy) is None
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_track:' + buy['trade_id'])['mark_count'] == 0
        assert c.execute("SELECT count(*) FROM audit WHERE action='position_mark'").fetchone()[0] == 0


def test_partial_sell_never_creates_a_second_request_in_position_loop(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id'], reason='stop_loss'))
    assert flow.store.feedback(*flow.fill(sell, status='PARTIAL', delta=-400, cash=20_000_000))
    for _ in range(3): manager.tick()
    assert flow.trade(buy)['remaining_raw'] == '600'
    assert flow.store.pending() is None
    events = [e for e in flow.store.status()['events'] if e['side'] == 'SELL']
    assert len(events) == 1 and events[0]['request_id'] == sell['request_id']


def test_owned_thread_shutdown_is_bounded_under_a_writer_lock(flow):
    manager = PositionManager(flow.store)
    writer = sqlite3.connect(flow.store.path, isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    try:
        manager.start()
        # Observe lock acquisition rather than guessing whether the tick began.
        end = time.monotonic() + 1
        while not manager._lock.locked() and time.monotonic() < end: time.sleep(.01)
        assert manager._lock.locked()
        started = time.monotonic()
        manager.stop()
        assert time.monotonic()-started < 3
        assert not manager._thread.is_alive() and manager.state['status'] == 'UNKNOWN'
    finally:
        writer.rollback(); writer.close()


def test_duplicate_ticks_do_not_duplicate_marks_or_requests(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 44_000_000)
    for _ in range(3): manager.tick()
    with flow.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM audit WHERE action='position_mark'").fetchone()[0] == 1
    assert len([e for e in flow.store.status()['events'] if e['side'] == 'SELL']) == 1
    assert intent(flow, buy)['reason'] == 'stop_loss'


def test_original_quote_budget_is_not_renewed_at_busy_commit(flow, monkeypatch):
    flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 44_000_000)
    flow.now += timedelta(seconds=1.93)
    readers, transactions = [], 0
    original = flow.store.transaction
    @contextmanager
    def lock_after_intent_commit(**kwargs):
        nonlocal transactions
        transactions += 1
        index = transactions
        with original(**kwargs) as c:
            yield c
        if index == 1:
            reader = sqlite3.connect(flow.store.path, isolation_level=None)
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM meta').fetchone()
            readers.append(reader)
    monkeypatch.setattr(flow.store, 'transaction', lock_after_intent_commit)
    started = time.monotonic()
    try:
        with pytest.raises(StateError, match='position_mark_changed_or_expired'):
            manager.tick()
        assert time.monotonic()-started < .6
    finally:
        for reader in readers: reader.rollback(); reader.close()
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_track:' + flow.store.status(connection=c)['trades'][0]['trade_id'])['mark_count'] == 0


def test_slow_first_quote_query_spends_the_original_budget(flow, monkeypatch):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 44_000_000)
    flow.now += timedelta(seconds=1.95)
    original = flow.store.meta
    delayed_once = False
    def delayed(c, key):
        nonlocal delayed_once
        if key == 'equity' and not delayed_once:
            delayed_once = True
            time.sleep(.09)
        return original(c, key)
    monkeypatch.setattr(flow.store, 'meta', delayed)
    manager.tick()
    assert intent(flow, buy) is None
    assert manager.state['valuation_state'] == 'UNKNOWN'
    manager.tick()  # same quote/held wall cannot receive another .05 seconds
    assert intent(flow, buy) is None
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_track:' + buy['trade_id'])['mark_count'] == 0


def test_time_exit_survives_a_subsequent_expired_price_commit(flow, monkeypatch):
    buy = flow.open()
    manager = PositionManager(flow.store)
    flow.now += timedelta(seconds=180)
    assert flow.reconcile()
    mark(flow, 44_000_000)
    original = flow.store.set_meta
    def delayed(c, key, value):
        result = original(c, key, value)
        if key == 'position_manager_at': flow.now += timedelta(seconds=3)
        return result
    monkeypatch.setattr(flow.store, 'set_meta', delayed)
    with pytest.raises(StateError, match='position_mark_changed_or_expired'):
        manager.tick()
    assert intent(flow, buy)['reason'] == 'maximum_hold'
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_track:' + buy['trade_id'])['mark_count'] == 0


def test_exit_intent_blocks_a_prepared_buy_at_all_central_boundaries(flow, monkeypatch, tmp_path):
    flow.store.cfg['max_open_positions'] = 4
    buy = flow.open()
    other = flow.buy(mint='other')
    manager = PositionManager(flow.store)
    flow.store.prepare_sell(buy['trade_id'], reason='strategy_exit')
    assert flow.store.pending()['side'] == 'SELL'
    with pytest.raises(StateError, match='position_exit_pending'):
        flow.claim(other)
    assert flow.store.prepare_buy(flow.candidate(mint='third')) is None
    gate = tmp_path / 'gate.json'
    gate.write_text('{"schema_version":1,"OWNER_BROADCAST_GATE":"OFF"}')
    risk = RiskEngine(flow.store, gate_path=gate, clock=flow.clock)
    assert 'position_exit_pending' in risk.assess(heartbeats={}, capabilities={})['failures']
    monkeypatch.setattr(gateway, '_LEDGER', flow.store)
    monkeypatch.setattr(gateway, '_POSITIONS', manager)
    monkeypatch.setattr(gateway, 'DB', tmp_path / 'absent.db')
    assert gateway.next_event()['side'] == 'SELL'


def test_current_inventory_disagreement_commits_kill_and_trigger_without_cas_failure(flow):
    buy = flow.open()
    assert not flow.reconcile(chain_positions={'mint':'999'})
    status = flow.store.status()
    assert 'position_balance_disagreement' in status['kill_reasons']
    with flow.store.transaction() as c:
        trigger = flow.store.meta(c, 'position_risk_exit:' + buy['trade_id'])
        assert trigger['cause'] == 'position_balance_disagreement'
    manager = PositionManager(flow.store)
    manager.tick()
    assert intent(flow, buy)['reason'] == 'risk_exit'
    assert flow.store.pending() is None


def test_same_live_quote_cannot_be_refreshed_by_another_tick(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    mark(flow, 50_000_000)
    flow.now += timedelta(seconds=1.95)
    manager.tick()
    assert manager.state['valuation_state'] == 'VERIFIED'
    time.sleep(.08)
    manager.tick()
    assert manager.state['valuation_state'] == 'UNKNOWN'
    assert intent(flow, buy) is None


def test_missing_or_dead_owned_thread_never_supplies_a_heartbeat(flow):
    manager = PositionManager(flow.store)
    manager.tick()
    assert manager.state['status'] == 'PASS'
    assert manager.heartbeat()['status'] == 'UNKNOWN'
    manager.start()
    try:
        until = time.monotonic()+2
        while manager.heartbeat()['status'] != 'PASS' and time.monotonic() < until: time.sleep(.01)
        assert manager.heartbeat()['status'] == 'PASS'
        manager.state['valid_until_monotonic'] = 0
        assert manager.heartbeat()['status'] == 'UNKNOWN'
    finally:
        manager.stop()
    assert manager.heartbeat()['status'] == 'UNKNOWN'


@pytest.mark.parametrize('reason', ['not_started', 'dead', 'stopped', 'busy', 'expired', 'tick_failed', None])
def test_owned_heartbeat_diagnoses_the_same_status_without_mutating_state(flow, monkeypatch, reason):
    manager = PositionManager(flow.store)
    manager.tick()
    class Worker:
        def is_alive(self):
            return reason != 'dead'
    manager._thread = None if reason == 'not_started' else Worker()
    if reason == 'stopped':
        manager._stop.set()
    if reason == 'busy':
        manager._lock.acquire()
    manager.state['valid_until_monotonic'] = time.monotonic() + 30
    if reason in ('expired', 'tick_failed'):
        manager.state['valid_until_monotonic'] = 0
    if reason == 'tick_failed':
        manager.state.update(status='UNKNOWN', last_error='StateError')
    before = dict(manager.state)
    try:
        result = manager.heartbeat()
        assert result['status'] == ('PASS' if reason is None else 'UNKNOWN')
        assert result['reason'] == reason
        assert result['last_error'] == before['last_error']
        assert manager.state == before
    finally:
        if reason == 'busy':
            manager._lock.release()


def test_future_buy_receipt_is_not_a_hold_clock(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    with flow.store.transaction() as c:
        receipt = flow.store.get(c, 'receipts', 'b'*88)
        receipt['proof']['block_time'] = (flow.now+timedelta(seconds=1)).isoformat()
        flow.store.put(c, 'receipts', 'b'*88, receipt)
    with pytest.raises(StateError, match='invalid_timestamp'):
        manager.tick()
    assert manager.state['status'] == 'UNKNOWN'
    assert intent(flow, buy) is None


def test_owned_loop_recovers_real_ledger_writer_contention_without_caller_ticks(flow, monkeypatch):
    manager = PositionManager(flow.store)
    held, release, entered = threading.Event(), threading.Event(), threading.Event()
    ticks = []
    original = manager.tick
    def tick():
        ticks.append(threading.current_thread())
        entered.set()
        return original()
    monkeypatch.setattr(manager, 'tick', tick)
    def writer():
        with flow.store.transaction():
            held.set()
            assert release.wait(2)
    blocker = threading.Thread(target=writer)
    blocker.start()
    assert held.wait(1)
    manager.start()
    try:
        assert entered.wait(1)
        until = time.monotonic() + 1
        while not manager._lock.locked() and time.monotonic() < until:
            time.sleep(.005)
        assert manager.heartbeat()['reason'] == 'busy'
        release.set()
        assert manager.require_current()['status'] == 'PASS'
        first = len(ticks)
        for _ in range(3):
            assert manager.require_current()['status'] == 'PASS'
        time.sleep(.55)
        assert manager.require_current()['status'] == 'PASS'
        assert len(ticks) > first
        assert all(worker is manager._thread for worker in ticks)
    finally:
        release.set()
        blocker.join(2)
        manager.stop()
    with pytest.raises(StateError, match='position_manager_health_unknown'):
        manager.require_current()


@pytest.mark.parametrize('state', ['expired', 'stopped', 'dead', 'tick_failed'])
def test_bounded_probe_rechecks_original_evidence_after_wait(flow, state):
    manager = PositionManager(flow.store)
    manager.tick()
    class Worker:
        def is_alive(self):
            return state != 'dead'
    manager._thread = Worker()
    observed = manager.state['observed_at']
    manager.state['valid_until_monotonic'] = time.monotonic() + .01
    deadline = manager.state['valid_until_monotonic']
    if state == 'stopped':
        manager._stop.set()
    if state == 'tick_failed':
        manager.state.update(status='UNKNOWN', last_error='StateError')
    manager._lock.acquire()
    def unlock():
        time.sleep(.03)
        manager._lock.release()
    releaser = threading.Thread(target=unlock)
    releaser.start()
    try:
        result = manager.heartbeat(wait_seconds=.2)
        assert result['status'] == 'UNKNOWN' and result['reason'] == state
        assert result['observed_at'] == observed
        assert result['valid_until_monotonic'] == deadline
        assert not manager._lock.locked()
        with pytest.raises(StateError, match='position_manager_health_unknown'):
            manager.require_current()
    finally:
        releaser.join(1)


def test_bounded_probe_timeout_never_reads_unlocked_state(flow):
    manager = PositionManager(flow.store)
    class Unreadable:
        def keys(self):
            pytest.fail('busy probe cannot copy in-flight state')
    manager.state = Unreadable()
    manager._lock.acquire()
    try:
        started = time.monotonic()
        assert manager.heartbeat(wait_seconds=.02)['reason'] == 'busy'
        assert time.monotonic() - started < .3
    finally:
        manager._lock.release()


def test_audit_index_is_installed_on_existing_ledger_without_changing_evidence(flow):
    with flow.store.transaction() as c:
        before = [tuple(r) for r in c.execute('SELECT * FROM audit ORDER BY id')]
        c.execute('DROP INDEX audit_action_trade_id')
    Ledger(flow.store.path, dict(flow.store.cfg), clock=flow.clock)
    with flow.store.transaction() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM audit ORDER BY id')] == before
        plan = c.execute("EXPLAIN QUERY PLAN SELECT at,doc FROM audit WHERE action='position_risk_trigger' AND trade_id=?", ('x',)).fetchall()
        assert any('SEARCH' in r['detail'] and 'audit_action_trade_id' in r['detail'] for r in plan)


@pytest.mark.parametrize('definition', [
    'CREATE UNIQUE INDEX audit_action_trade_id ON audit(action,trade_id,id)',
    'CREATE INDEX audit_action_trade_id ON audit(trade_id,action,id)',
    "CREATE INDEX audit_action_trade_id ON audit(action,trade_id,id) WHERE action='unused'",
    'CREATE INDEX audit_action_trade_id ON meta(key,value)'])
def test_wrong_existing_audit_index_is_rejected_without_replacement(flow, definition):
    with flow.store.transaction() as c:
        c.execute('DROP INDEX audit_action_trade_id')
        c.execute(definition)
    with pytest.raises(StateError, match='invalid_audit_index'):
        Ledger(flow.store.path, dict(flow.store.cfg), clock=flow.clock)


def test_owned_loop_values_open_position_and_persists_dynamic_exit(flow):
    buy = flow.open()
    mark(flow, 55_000_000)
    manager = PositionManager(flow.store)
    manager.start()
    try:
        until = time.monotonic() + 2
        while manager.heartbeat(wait_seconds=.1)['status'] != 'PASS' and time.monotonic() < until:
            time.sleep(.02)
        first = manager.require_current()
        assert first['positions'] == 1 and first['valuation_state'] == 'VERIFIED'
        assert intent(flow, buy) is None
        mark(flow, 51_699_999)
        until = time.monotonic() + 2
        saved = None
        while saved is None and time.monotonic() < until:
            time.sleep(.05)
            saved = intent(flow, buy)
        assert saved['reason'] == 'trailing_stop'
        current = manager.require_current()
        assert current['status'] == 'PASS'
        assert current['valid_until_monotonic'] > first['valid_until_monotonic']
        assert flow.store.pending()['amount_raw'] == '1000'
        assert current['broadcast'] is False
        assert flow.trade(buy)['remaining_raw'] == '1000'
        # This proves the real owned code on controlled receipt/quote evidence,
        # not a production fill or SEND.
    finally:
        manager.stop()
    assert manager.heartbeat()['status'] == 'UNKNOWN'


@pytest.mark.parametrize('delay', ['monotonic','wall_reconciliation'])
def test_final_composite_guard_rechecks_every_position_after_last_expensive_guard(flow, monkeypatch, delay):
    flow.store.cfg['max_open_positions'] = 4
    first = flow.open()
    second = flow.claim(flow.buy(mint='other'))
    assert flow.store.feedback(*flow.fill(second, signature='c'*88))
    assert flow.reconcile()
    manager = PositionManager(flow.store)
    if delay == 'monotonic':
        earlier = flow.clock()
        flow.now += timedelta(seconds=1.9)
        observation = observe(flow)
        observation['marks'][first['trade_id']]['quoted_at'] = earlier
    else:
        flow.now += timedelta(seconds=14.9)
        observation = observe(flow)
    flow.store.mark_equity(observation)
    original, calls = manager._mark, {}
    def spend_first_budget_in_second_guard(c, trade):
        tid = trade['trade_id']
        calls[tid] = calls.get(tid, 0)+1
        result = original(c, trade)
        if tid == second['trade_id'] and calls[tid] == 2:
            if delay == 'monotonic': time.sleep(.13)
            else: flow.now += timedelta(seconds=.3)
        return result
    monkeypatch.setattr(manager, '_mark', spend_first_budget_in_second_guard)
    with pytest.raises(StateError, match='position_mark_changed_or_expired'):
        manager.tick()
    with flow.store.transaction() as c:
        for buy in (first, second):
            assert flow.store.meta(c, 'position_track:' + buy['trade_id'])['mark_count'] == 0
        assert c.execute("SELECT count(*) FROM audit WHERE action='position_mark'").fetchone()[0] == 0


def test_restart_before_first_buy_receipt_preserves_attempt_obligation_without_guessing_inventory(flow):
    event = flow.claim(flow.buy())
    assert flow.trade(event)['remaining_raw'] == '0'
    flow.store.recover()
    with flow.store.transaction() as c:
        obligation = flow.store.meta(c, 'position_recovery:' + event['event_id'])
        assert obligation['request_id'] == event['request_id']
    assert intent(flow, event) is None
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    assert flow.store.feedback(*flow.fill(event))
    assert flow.reconcile()
    assert intent(flow, event)['reason'] == 'recovery_exit'
    sell = flow.store.pending()
    assert sell['trade_id'] == event['trade_id'] and sell['amount_raw'] == '1000'
    events = flow.store.status()['events']
    assert len([e for e in events if e['side'] == 'BUY']) == 1
    assert len([e for e in events if e['side'] == 'SELL']) == 1


def test_lost_recovery_obligation_cannot_be_recreated_after_restart(flow):
    event = flow.claim(flow.buy())
    flow.store.recover()
    with flow.store.transaction() as c:
        c.execute('DELETE FROM meta WHERE key=?', ('position_recovery:' + event['event_id'],))
    with pytest.raises(StateError, match='position_recovery_obligation_lost'):
        Ledger(flow.store.path, clock=flow.clock)


def test_negative_total_equity_commits_unknown_and_retains_historical_peak(flow):
    buy = flow.open()
    manager = PositionManager(flow.store)
    before = mark(flow, 55_000_000)
    assert mark(flow, 1, 2_000_000_000) is None
    status = flow.store.status()
    assert status['equity']['fresh'] is False and status['equity']['valuation_state'] == 'UNKNOWN'
    assert status['equity']['peak_lamports'] == before['peak_lamports']
    assert status['equity_invalid']['projected_lamports'] == '-1049999999'
    assert 'max_drawdown_15pct' in status['kill_reasons']
    manager.tick()
    assert manager.state['valuation_state'] == 'UNKNOWN'
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_track:' + buy['trade_id'])['mark_count'] == 0
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    assert not flow.reconcile()
    assert flow.store.status()['equity']['fresh'] is False
    after = mark(flow, 50_000_000)
    assert after['peak_lamports'] == before['peak_lamports']
    assert float(after['max_drawdown']) > 1
    assert flow.store.status()['equity_invalid'] is None
    assert not flow.store.status()['new_buys_enabled']


def test_lost_equity_invalidity_flag_never_revives_the_old_mark(flow):
    flow.open()
    mark(flow, 55_000_000)
    assert mark(flow, 1, 2_000_000_000) is None
    with flow.store.transaction() as c:
        c.execute("DELETE FROM meta WHERE key='equity_invalid'")
    with pytest.raises(StateError, match='equity_invalidation_lost'):
        flow.store.status()


def test_lost_risk_trigger_cannot_be_cleared_or_reinitialized(flow):
    buy = flow.open()
    flow.store.block('api_failure_limit')
    key = 'position_risk_exit:' + buy['trade_id']
    with flow.store.transaction() as c:
        c.execute('DELETE FROM meta WHERE key=?', (key,))
    with pytest.raises(StateError, match='position_risk_trigger_lost'):
        flow.store.status()
    with pytest.raises(StateError, match='position_risk_trigger_lost'):
        Ledger(flow.store.path, clock=flow.clock)
    flow.store.block('max_drawdown_15pct')
    with flow.store.transaction() as c:
        assert flow.store.meta(c, key) is None
        assert 'max_drawdown_15pct' in flow.store.meta(c, 'kill_reasons')
        assert c.execute("SELECT count(*) FROM audit WHERE action='position_risk_trigger'").fetchone()[0] == 1


@pytest.mark.parametrize('phase', ['first_sql', 'audit'])
def test_negative_equity_persists_even_if_original_deadline_expires(flow, monkeypatch, phase):
    flow.open()
    mark(flow, 55_000_000)
    observation = observe(flow, value='1', costs='2000000000')
    deadline = time.monotonic() + .08
    if phase == 'first_sql':
        original = flow.store.set_meta
        def delayed(c, key, value):
            if key == 'equity_invalid':
                assert flow.store.valuation_fault()[0]
                assert c.safety_commit_deadline > deadline
                time.sleep(.1)
            original(c, key, value)
        monkeypatch.setattr(flow.store, 'set_meta', delayed)
    else:
        original = flow.store.audit
        def delayed(c, action, *args):
            if action == 'equity_invalid': time.sleep(.1)
            original(c, action, *args)
        monkeypatch.setattr(flow.store, 'audit', delayed)
    assert flow.store.mark_equity(observation, deadline=deadline) is None
    assert time.monotonic() > deadline
    assert flow.store.status()['equity']['fresh'] is False
    restarted = Ledger(flow.store.path, clock=flow.clock)
    assert restarted.status()['equity_invalid']['projected_lamports'] == '-1049999999'
    assert 'max_drawdown_15pct' in restarted.status()['kill_reasons']


@pytest.mark.parametrize('with_deadline', [False, True])
def test_safety_commit_waits_for_real_reader_without_renewing_budget(flow, monkeypatch, with_deadline):
    flow.open()
    mark(flow, 55_000_000)
    observation = observe(flow, value='1', costs='2000000000')
    original = LedgerConnection.arm_safety_commit
    locked, release = threading.Event(), threading.Event()
    failures = []
    def reader():
        try:
            c = sqlite3.connect(flow.store.path, isolation_level=None)
            c.execute('BEGIN')
            c.execute('SELECT value FROM meta').fetchall()
            locked.set()
            release.wait(2)
            c.rollback()
            c.close()
        except BaseException as error: failures.append(error)
    threads = []
    def arm(c, budget):
        original(c, budget)
        deadline = c.safety_commit_deadline
        thread = threading.Thread(target=reader)
        threads.append(thread)
        thread.start()
        assert locked.wait(1)
        threading.Timer(.15, release.set).start()
        c.commit_guards.append(lambda: assert_same_deadline(c, deadline))
    def assert_same_deadline(c, expected):
        assert c.safety_commit_deadline == expected
    monkeypatch.setattr(LedgerConnection, 'arm_safety_commit', arm)
    began = time.monotonic()
    try:
        assert flow.store.mark_equity(observation, deadline=began+.08 if with_deadline else None) is None
    finally:
        release.set()
        for thread in threads: thread.join(2)
    assert not failures and time.monotonic()-began >= .14
    assert flow.store.status()['equity_invalid'] is not None


def test_early_safety_io_failure_revokes_existing_send_permission(flow, tmp_path, monkeypatch):
    from test_execution_adapter import ready
    from execution_adapter import build_swap_payload
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch, side='SELL')
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    grant = risk.authorize_send(lease, risk.assess(**probes('SELL'), side='SELL', event=claimed, quote=quote, lease=lease))
    observation = observe(flow, value='1', costs='2000000000')
    original = flow.store.set_meta
    def fail(c, key, value):
        if key == 'equity_invalid': raise sqlite3.OperationalError('isolated_io_failure')
        original(c, key, value)
    monkeypatch.setattr(flow.store, 'set_meta', fail)
    with pytest.raises(sqlite3.OperationalError, match='isolated_io_failure'):
        flow.store.mark_equity(observation)
    status = flow.store.status()
    assert status['equity_invalid'] is None  # rollback really occurred
    assert status['equity']['fresh'] is False
    assert status['equity']['invalid_reason'] == 'valuation_persistence_unknown'
    assert status['new_buys_enabled'] is status['signals_enabled'] is False
    with flow.store.transaction() as c:
        assert not flow.store.buy_allowed(c)
    with pytest.raises(StateError, match='dispatch_guard_changed'):
        risk.dispatch_contract(grant)
    assert not flow.store.status()['events'][-1].get('order_id')


def test_valuation_fault_blocks_pending_and_claim_buy_even_after_rollback(flow):
    event = flow.buy()
    flow.store.invalidate_valuation()
    assert flow.store.pending() is None
    assert flow.store.prepare_buy(flow.candidate(mint='other')) is None
    with pytest.raises(StateError, match='equity_valuation_unknown'):
        flow.claim(event)
    assert flow.trade(event)['execution_state'] == 'READY'


def test_valid_equity_clears_fault_only_after_commit_and_not_newer_generation(flow, monkeypatch):
    flow.open()
    flow.store.invalidate_valuation()
    original = flow.store.clear_valuation_after_commit
    calls = []
    def newer_fault(generation):
        # Callback runs after COMMIT: another reader can see the valid mark.
        c = sqlite3.connect(flow.store.path)
        assert json.loads(c.execute("SELECT value FROM meta WHERE key='equity'").fetchone()[0])['current_lamports'] == '1000000000'
        c.close()
        flow.store.invalidate_valuation()
        original(generation)
        calls.append(generation)
    monkeypatch.setattr(flow.store, 'clear_valuation_after_commit', newer_fault)
    mark(flow, 50_000_000)
    assert calls == [1] and flow.store.valuation_fault() == (True, 2)
    assert flow.store.status()['equity']['fresh'] is False
    monkeypatch.setattr(flow.store, 'clear_valuation_after_commit', original)
    mark(flow, 50_000_000)
    assert flow.store.valuation_fault() == (False, 2)
    assert flow.store.status()['equity']['fresh'] is True


def test_expired_positive_mark_rolls_back_and_cannot_clear_fault(flow, monkeypatch):
    flow.open()
    before = mark(flow, 55_000_000)
    flow.store.invalidate_valuation()
    observation = observe(flow)
    original = flow.store.audit
    def expire(c, action, *args):
        if action == 'equity_mark': time.sleep(.1)
        original(c, action, *args)
    monkeypatch.setattr(flow.store, 'audit', expire)
    with pytest.raises(StateError, match='reconciliation_read_deadline'):
        flow.store.mark_equity(observation, deadline=time.monotonic()+.08)
    assert flow.store.valuation_fault() == (True, 1)
    assert flow.store.status()['equity']['current_lamports'] == before['current_lamports']


@pytest.mark.parametrize('ttl', [30, 30.1, 1000])
def test_execution_quote_ttl_cannot_exceed_safety_commit_bound(tmp_path, ttl):
    cfg = dict(load_config(), quote_max_age_seconds=ttl)
    with pytest.raises(StateError, match='unbounded_execution_quote_age'):
        Ledger(tmp_path / 'invalid.db', cfg)


def test_exhausted_safety_commit_cannot_restore_buy_or_old_fresh_equity(flow, monkeypatch):
    flow.store.cfg['ledger_lock_timeout_seconds'] = .1
    flow.store.cfg['quote_max_age_seconds'] = .05
    flow.store.cfg['max_open_positions'] = 2
    flow.open()
    other = flow.buy(mint='other')
    mark(flow, 55_000_000)
    observation = observe(flow, value='1', costs='2000000000')
    original = LedgerConnection.arm_safety_commit
    readers = []
    def arm(c, budget):
        assert budget == 2.5
        original(c, budget)
        reader = sqlite3.connect(flow.store.path, isolation_level=None)
        reader.execute('BEGIN')
        reader.execute('SELECT value FROM meta').fetchall()
        readers.append(reader)
    monkeypatch.setattr(LedgerConnection, 'arm_safety_commit', arm)
    began = time.monotonic()
    try:
        with pytest.raises(StateError, match='reconciliation_read_deadline'):
            flow.store.mark_equity(observation, deadline=began+.08)
    finally:
        for reader in readers:
            reader.rollback()
            reader.close()
    assert 2.4 <= time.monotonic()-began < 3.5
    assert flow.store.status()['equity_invalid'] is None
    assert flow.store.status()['equity']['fresh'] is False
    assert flow.store.pending() is None
    with pytest.raises(StateError, match='equity_valuation_unknown'):
        flow.claim(other)
    restarted = Ledger(flow.store.path, clock=flow.clock)
    restarted.recover()
    assert restarted.status()['reconciliation_required'] is True
    assert restarted.status()['new_buys_enabled'] is False
