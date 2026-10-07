"""All SELL paths share current inventory evidence. Isolated ledgers only."""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_execution import flow
from execution_state import Ledger, StateError, canonical


def exits(flow):
    return [e for e in flow.store.status(expire=False)['events'] if e['side'] == 'SELL']


@pytest.mark.parametrize('path', ['prepare', 'emergency'])
def test_stale_reconciliation_saves_intent_without_materializing_sell(flow, path):
    buy = flow.open()
    flow.now += timedelta(seconds=16)
    if path == 'emergency':
        assert flow.store.control('emergency_exit')
        reason = 'emergency_exit'
    else:
        assert flow.store.prepare_sell(buy['trade_id'], reason='stop_loss') is None
        reason = 'stop_loss'
    assert exits(flow) == []
    with flow.store.transaction() as c:
        before = flow.store.meta(c, 'position_exit:' + buy['trade_id'])
        assert before['reason'] == reason
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    assert flow.store.pending() is None
    assert flow.reconcile()
    event = flow.store.pending()
    assert event['side'] == 'SELL' and event['amount_raw'] == '1000'
    assert event['trade_id'] == buy['trade_id'] and event['exit_reason'] == reason
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_exit:' + buy['trade_id']) == before
    assert len(exits(flow)) == 1


def test_ready_sell_is_not_returned_or_claimed_after_reconciliation_expires(flow):
    buy = flow.open()
    sell = flow.store.prepare_sell(buy['trade_id'])
    flow.now += timedelta(seconds=16)
    assert flow.store.pending() is None
    with pytest.raises(StateError, match='sell_without_current_reconciliation'):
        flow.claim(sell)
    assert exits(flow)[0]['state'] == 'READY' and exits(flow)[0].get('sent_at') is None
    assert flow.reconcile()
    assert flow.store.pending() == sell
    assert flow.claim(sell)['state'] == 'SENT'


def test_unknown_other_order_blocks_sell_without_losing_exit_intent(flow):
    flow.store.cfg['max_open_positions'] = 4
    buy = flow.open()
    other = flow.claim(flow.buy(mint='other'))
    assert flow.store.prepare_sell(buy['trade_id'], reason='risk_exit') is None
    assert exits(flow) == []
    assert flow.store.feedback(*flow.fill(other, signature='c'*88))
    assert flow.reconcile()
    sell = flow.store.pending()
    assert sell['trade_id'] == buy['trade_id'] and sell['exit_reason'] == 'risk_exit'
    assert sell['amount_raw'] == '1000'


def test_partial_sell_restart_preserves_reason_and_never_creates_second_attempt(flow):
    buy = flow.open()
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id'], reason='stop_loss'))
    assert flow.store.feedback(*flow.fill(sell, status='PARTIAL', delta=-400, cash=20000000))
    assert flow.store.prepare_sell(buy['trade_id'], reason='emergency_exit') is None
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    assert flow.store.materialize_exit(buy['trade_id']) is None
    assert flow.trade(buy)['remaining_raw'] == '600'
    assert len(exits(flow)) == 1 and exits(flow)[0]['request_id'] == sell['request_id']
    with flow.store.transaction() as c:
        assert flow.store.exit_reason(c, flow.store.get(c, 'trades', buy['trade_id'])) == 'emergency_exit'


@pytest.mark.parametrize('reason', ['emergency_exit','risk_exit','session_exit','recovery_exit',
                                   'stop_loss','maximum_hold','take_profit','strategy_exit'])
def test_intent_reason_survives_reconciliation_materialization(flow, reason):
    buy = flow.claim(flow.buy())
    assert flow.store.feedback(*flow.fill(buy))
    assert flow.store.prepare_sell(buy['trade_id'], reason=reason) is None
    assert flow.reconcile()
    sell = flow.store.pending()
    assert sell['exit_reason'] == reason and sell['amount_raw'] == '1000'
    assert len(exits(flow)) == 1


@pytest.mark.parametrize('field,value', [('wallet','foreign'),('mint','foreign'),('trade_id','foreign'),
    ('reason','not-a-reason'),('requested_at','invalid')])
def test_corrupt_intent_is_rejected_and_not_overwritten(flow, field, value):
    buy = flow.open()
    flow.store.prepare_sell(buy['trade_id'])
    with flow.store.transaction() as c:
        key = 'position_exit:' + buy['trade_id']
        broken = flow.store.meta(c, key)
        broken[field] = value
        flow.store.set_meta(c, key, broken)
    with pytest.raises(StateError):
        flow.store.status()
    with pytest.raises(StateError):
        flow.store.prepare_sell(buy['trade_id'], reason='emergency_exit')
    with flow.store.transaction() as c:
        assert flow.store.meta(c, key) == broken


@pytest.mark.parametrize('mutation', ['duplicate','generation','timestamp','quantity','foreign_order'])
def test_current_audit_anchor_is_unique_coherent_and_contains_no_foreign_order(flow, mutation):
    buy = flow.open()
    with flow.store.transaction() as c:
        row = c.execute("SELECT id,at,doc FROM audit WHERE action='reconciliation' ORDER BY id DESC LIMIT 1").fetchone()
        body = json.loads(row['doc'])
        if mutation == 'duplicate':
            c.execute('INSERT INTO audit(at,action,trade_id,doc) VALUES(?,?,?,?)',
                      (row['at'], 'reconciliation', None, row['doc']))
        else:
            if mutation == 'generation': body['generation'] += 1
            elif mutation == 'timestamp': body['observation']['observed_at'] = '2000-01-01T00:00:00+00:00'
            elif mutation == 'quantity': body['trade_positions'][buy['trade_id']] = '999'
            else: body['observation']['orders']['foreign'] = {'state':'UNKNOWN','order_id':'foreign'}
            c.execute('UPDATE audit SET doc=? WHERE id=?', (canonical(body), row['id']))
    if mutation == 'foreign_order':
        assert flow.store.prepare_sell(buy['trade_id'], reason='risk_exit') is None
    else:
        with pytest.raises(StateError, match='sell_reconciliation_'):
            flow.store.prepare_sell(buy['trade_id'], reason='risk_exit')
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_exit:' + buy['trade_id'])['reason'] == 'risk_exit'
        assert not any(e['side'] == 'SELL' for e in flow.store.all(c, 'events'))


def test_materialization_commit_expiry_rolls_back_event_but_preserves_prior_intent(flow, monkeypatch):
    buy = flow.open()
    original = flow.store.transaction
    connections = []
    @contextmanager
    def delayed(**kwargs):
        with original(**kwargs) as c:
            connections.append(c)
            yield c
            if c.commit_guards:
                flow.now += timedelta(seconds=16)
    monkeypatch.setattr(flow.store, 'transaction', delayed)
    assert flow.store.prepare_sell(buy['trade_id'], reason='maximum_hold') is None
    assert all(c.commit_guards == [] for c in connections)
    with original() as c:
        assert flow.store.meta(c, 'position_exit:' + buy['trade_id'])['reason'] == 'maximum_hold'
        assert not any(e['side'] == 'SELL' for e in flow.store.all(c, 'events'))
        assert not c.execute("SELECT 1 FROM audit WHERE action='event_ready' AND json_extract(doc,'$.side')='SELL'").fetchone()


def test_shared_reader_commit_wait_cannot_renew_original_reconciliation_budget(flow):
    buy = flow.open()
    flow.store.cfg['reconciliation_max_age_seconds'] = .12
    with flow.store.transaction() as c:
        flow.store.request_exit(c, flow.store.get(c, 'trades', buy['trade_id']), 'risk_exit')
    reader = sqlite3.connect(flow.store.path, isolation_level=None)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM meta').fetchone()
    started = time.monotonic()
    try:
        assert flow.store.materialize_exit(buy['trade_id']) is None
    finally:
        reader.rollback()
        reader.close()
    assert .10 <= time.monotonic()-started < 1
    assert exits(flow) == []
    with flow.store.transaction() as c:
        assert flow.store.exit_reason(c, flow.store.get(c, 'trades', buy['trade_id'])) == 'risk_exit'


def test_parallel_prepare_and_emergency_use_one_sell_request(flow):
    buy = flow.open()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda reason: flow.store.prepare_sell(buy['trade_id'], reason=reason),
                                ['strategy_exit','stop_loss','emergency_exit']))
    assert len({e['request_id'] for e in results}) == 1 and len(exits(flow)) == 1
    assert exits(flow)[0]['amount_raw'] == '1000'
    with flow.store.transaction() as c:
        assert flow.store.exit_reason(c, flow.store.get(c, 'trades', buy['trade_id'])) == 'emergency_exit'


def test_request_cannot_create_exit_for_unbought_candidate(flow):
    tid = flow.candidate()
    with pytest.raises(StateError, match='exit_requires_confirmed_inventory'):
        with flow.store.transaction() as c:
            flow.store.request_exit(c, flow.store.get(c, 'trades', tid), 'emergency_exit')
    assert exits(flow) == []


def test_schema_migration_preserves_valid_closed_exit_intent_and_timestamp(flow, monkeypatch):
    import execution_policy
    from test_execution_machine import downgrade
    monkeypatch.setattr(execution_policy, 'owner_gate', lambda: {'valid':True,'value':'OFF'})
    buy = flow.open()
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id'], reason='stop_loss'))
    assert flow.store.feedback(*flow.fill(sell))
    assert flow.reconcile()
    with flow.store.transaction() as c:
        key = 'position_exit:' + buy['trade_id']
        before = flow.store.meta(c, key)
    downgrade(flow.store)
    migrated = Ledger(flow.store.path, clock=flow.clock, migrate_schema1=True)
    with migrated.transaction() as c:
        trade = migrated.get(c, 'trades', buy['trade_id'])
        assert trade['execution_state'] == 'CLOSED' and trade['exit_requested']
        assert migrated.meta(c, key) == before
        assert migrated.exit_reason(c, trade) == 'stop_loss'


@pytest.mark.parametrize('mutation', ['wallet','mint','trade_id','reason','timestamp','orphan','unbought'])
def test_migration_rejects_bad_or_orphan_exit_intents_atomically(flow, monkeypatch, mutation):
    import execution_policy
    from test_execution_machine import downgrade, raw_state
    monkeypatch.setattr(execution_policy, 'owner_gate', lambda: {'valid':True,'value':'OFF'})
    if mutation == 'unbought':
        tid = flow.candidate()
        with flow.store.transaction() as c:
            trade = flow.store.get(c, 'trades', tid)
            intent = {k: trade[k] for k in ('trade_id','wallet','mint')}
            intent.update(reason='strategy_exit', requested_at=flow.clock())
            flow.store.set_meta(c, 'position_exit:' + tid, intent)
    else:
        buy = flow.open()
        flow.store.prepare_sell(buy['trade_id'])
        with flow.store.transaction() as c:
            key = 'position_exit:' + buy['trade_id']
            intent = flow.store.meta(c, key)
            if mutation == 'orphan':
                flow.store.set_meta(c, 'position_exit:missing-trade', intent)
            else:
                field = 'requested_at' if mutation == 'timestamp' else mutation
                intent[field] = 123 if mutation == 'timestamp' else 'invalid'
                flow.store.set_meta(c, key, intent)
    downgrade(flow.store)
    before = raw_state(flow.store)
    with pytest.raises(StateError):
        Ledger(flow.store.path, clock=flow.clock, migrate_schema1=True)
    assert raw_state(flow.store) == before


def test_claim_commit_expiry_keeps_ready_request_without_send_marker(flow):
    buy = flow.open()
    sell = flow.store.prepare_sell(buy['trade_id'])
    flow.store.cfg['reconciliation_max_age_seconds'] = .12
    reader = sqlite3.connect(flow.store.path, isolation_level=None)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM meta').fetchone()
    started = time.monotonic()
    try:
        with pytest.raises(StateError, match='sell_reconciliation_expired'):
            flow.claim(sell)
    finally:
        reader.rollback()
        reader.close()
    assert .10 <= time.monotonic()-started < 1
    stored = exits(flow)[0]
    assert stored['state'] == 'READY' and stored['request_id'] == sell['request_id']
    assert stored.get('sent_at') is None and stored.get('send_started_at') is None
    assert flow.trade(buy)['execution_state'] == 'POSITION_OPEN'
    assert flow.trade(buy)['remaining_raw'] == '1000'


def test_clock_rollback_above_original_time_cannot_renew_commit_guards(flow, monkeypatch):
    buy = flow.open()
    with flow.store.transaction() as c:
        flow.store.request_exit(c, flow.store.get(c, 'trades', buy['trade_id']), 'risk_exit')
    reader = sqlite3.connect(flow.store.path, isolation_level=None)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM meta').fetchone()
    original = flow.store.transaction
    initial = flow.now
    hooks = []
    @contextmanager
    def altered(**kwargs):
        with original(**kwargs) as c:
            yield c
            if c.commit_guards:
                def clock_change():
                    hooks.append(1)
                    flow.now = initial+timedelta(seconds=3 if len(hooks)==1 else 2)
                c.commit_guards.insert(0, clock_change)
    monkeypatch.setattr(flow.store, 'transaction', altered)
    try:
        with pytest.raises(StateError, match='invalid_timestamp'):
            flow.store.materialize_exit(buy['trade_id'])
    finally:
        reader.rollback()
        reader.close()
    assert len(hooks) == 2 and exits(flow) == []


def test_audit_query_latency_consumes_budget_before_commit(flow, monkeypatch):
    buy = flow.open()
    original = flow.store.sell_anchor
    calls = []
    def slow(*args, **kwargs):
        anchor = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            flow.now += timedelta(seconds=16)
        return anchor
    monkeypatch.setattr(flow.store, 'sell_anchor', slow)
    assert flow.store.prepare_sell(buy['trade_id'], reason='take_profit') is None
    assert len(calls) == 2 and exits(flow) == []
    with flow.store.transaction() as c:
        assert flow.store.exit_reason(c, flow.store.get(c, 'trades', buy['trade_id'])) == 'take_profit'


def test_repeated_valid_reconciliation_accepts_current_anchor_with_old_position_record(flow):
    buy = flow.open()
    original = flow.trade(buy)['position_reconciliation_revision']
    assert flow.reconcile()
    assert flow.trade(buy)['position_reconciliation_revision'] == original
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'reconciliation_revision') != original
    sell = flow.store.prepare_sell(buy['trade_id'])
    assert sell['amount_raw'] == '1000' and flow.claim(sell)['state'] == 'SENT'


@pytest.mark.parametrize('phase', ['during_first_query','before_registration'])
def test_first_anchor_clock_rollback_above_audit_time_never_enlarges_budget(flow, monkeypatch, phase):
    buy = flow.open()
    initial = flow.now
    flow.now += timedelta(seconds=10)
    if phase == 'during_first_query':
        original = flow.store.require_reconciliation_record
        def rolled(*args):
            original(*args)
            flow.now = initial+timedelta(seconds=5)
        monkeypatch.setattr(flow.store, 'require_reconciliation_record', rolled)
    else:
        original = flow.store.sell_anchor
        def rolled(*args, **kwargs):
            anchor = original(*args, **kwargs)
            flow.now = initial+timedelta(seconds=5)
            return anchor
        monkeypatch.setattr(flow.store, 'sell_anchor', rolled)
    with pytest.raises(StateError, match='invalid_timestamp'):
        flow.store.prepare_sell(buy['trade_id'], reason='risk_exit')
    # Intent was committed at initial+10s; don't interpret an impossible
    # current wall time as valid state. Raw inspection proves durable intent.
    with flow.store.transaction() as c:
        assert flow.store.meta(c, 'position_exit:' + buy['trade_id'])['reason'] == 'risk_exit'
        assert not any(e['side']=='SELL' for e in flow.store.all(c, 'events'))


def test_slow_first_audit_query_spends_original_monotonic_budget(flow, monkeypatch):
    buy = flow.open()
    flow.now += timedelta(seconds=14, milliseconds=950)
    original = flow.store.require_reconciliation_record
    calls = []
    def slow(*args):
        original(*args)
        calls.append(1)
        if len(calls) == 1:
            time.sleep(.12)  # Wall clock frozen; only original monotonic TTL can reject.
    monkeypatch.setattr(flow.store, 'require_reconciliation_record', slow)
    assert flow.store.prepare_sell(buy['trade_id'], reason='stop_loss') is None
    assert len(calls) == 1 and exits(flow) == []
    with flow.store.transaction() as c:
        assert flow.store.exit_reason(c, flow.store.get(c, 'trades', buy['trade_id'])) == 'stop_loss'


def test_first_query_cannot_extend_reconciliation_limit_before_guard_registration(flow, monkeypatch):
    buy = flow.open()
    original = flow.store.require_reconciliation_record
    def changed(*args):
        original(*args)
        flow.store.cfg['reconciliation_max_age_seconds'] = 30
    monkeypatch.setattr(flow.store, 'require_reconciliation_record', changed)
    assert flow.store.prepare_sell(buy['trade_id'], reason='maximum_hold') is None
    assert exits(flow) == []
