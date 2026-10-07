"""Conservative liquidation marks on isolated reconciled ledger fixtures."""
from datetime import timedelta

import pytest

from test_execution import flow
from execution_state import Ledger, StateError


def observe(flow, value='50000000', *, costs='0'):
    status = flow.store.status()
    marks = {t['trade_id']: {'wallet': 'wallet', 'mint': t['mint'], 'remaining_raw': t['remaining_raw'],
             'minimum_output_lamports': value, 'exit_cost_budget_lamports': costs,
             'quoted_at': flow.clock(), 'source': 'GMGN_ORDER_QUOTE'}
             for t in status['trades'] if int(t['remaining_raw']) > 0}
    return {'wallet': 'wallet', 'balance_lamports': status['wallet_balance_lamports'],
            'reconciliation_revision': status['reconciliation_revision'],
            'observed_at': flow.clock(), 'marks': marks}


def test_equity_cash_baseline_and_realized_costs_are_not_double_charged(flow):
    before = flow.store.mark_equity(observe(flow))
    assert before['start_lamports'] == before['current_lamports'] == '1000000000'
    buy = flow.open()
    equity = flow.store.mark_equity(observe(flow, value='55000000', costs='5000000'))
    assert equity['current_lamports'] == '1000000000'
    assert equity['unrealized_pnl_lamports'] == '0'
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell))
    assert flow.reconcile()
    closed = flow.store.mark_equity(observe(flow))
    assert closed['current_lamports'] == '1005000000'
    assert closed['realized_pnl_lamports'] == '5000000'
    assert closed['unrealized_pnl_lamports'] == '0'
    with flow.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM audit WHERE action='equity_mark'").fetchone()[0] == 3


def test_dd_boundary_and_permanent_restart_guard_allow_exit(flow):
    buy = flow.open()
    peak = flow.store.mark_equity(observe(flow, value='250000000'))
    assert peak['peak_lamports'] == '1200000000'
    boundary = flow.store.mark_equity(observe(flow, value='70000000'))
    assert boundary['current_drawdown'] == '0.15'
    assert flow.store.status()['kill_reason'] is None
    breached = flow.store.mark_equity(observe(flow, value='69999999'))
    assert float(breached['max_drawdown']) > .15
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    assert 'max_drawdown_15pct' in flow.store.status()['kill_reasons']
    assert flow.reconcile() is False
    assert flow.store.control('reset_session') is False
    assert flow.store.prepare_buy(flow.candidate(mint='other')) is None
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id'], reason='risk_exit'))
    assert flow.store.feedback(*flow.fill(sell))
    assert flow.reconcile() is False
    assert flow.trade(sell)['state'] == 'CLOSED'
    # Recovery of equity cannot clear a safety kill or erase maximum DD.
    recovered = flow.store.mark_equity(observe(flow))
    assert recovered['max_drawdown'] == '0.1625'
    assert flow.store.status()['kill_reason'] == 'max_drawdown_15pct'


@pytest.mark.parametrize('field,value,error', [
    ('wallet', 'foreign', 'wallet_or_cash'),
    ('balance_lamports', '1', 'wallet_or_cash'),
    ('reconciliation_revision', 'wrong', 'inventory_revision'),
    ('marks', {}, 'incomplete_marks'),
])
def test_equity_rejects_unbound_observations(flow, field, value, error):
    flow.open()
    observation = observe(flow)
    observation[field] = value
    with pytest.raises(StateError, match=error):
        flow.store.mark_equity(observation)
    assert flow.store.status()['equity'] is None


@pytest.mark.parametrize('field,value,error', [
    ('remaining_raw', '999', 'quantity_mismatch'),
    ('source', 'ENTRY_PRICE', 'identity_mismatch'),
    ('mint', 'foreign', 'identity_mismatch'),
    ('exit_cost_budget_lamports', 1.2, 'invalid_raw_amount'),
])
def test_equity_rejects_wrong_marks(flow, field, value, error):
    buy = flow.open()
    observation = observe(flow)
    observation['marks'][buy['trade_id']][field] = value
    with pytest.raises(StateError, match=error):
        flow.store.mark_equity(observation)


def test_equity_stale_quote_and_partial_fill_fail_closed(flow):
    event = flow.claim(flow.buy())
    assert flow.store.feedback(*flow.fill(event, status='PARTIAL', delta=500, cash=-25000000, swap_input=25000000))
    with pytest.raises(StateError, match='requires_reconciliation'):
        flow.store.mark_equity(observe(flow))


def test_equity_freshness_and_inventory_mutation(flow):
    flow.open()
    observation = observe(flow)
    flow.store.mark_equity(observation)
    assert flow.store.status()['equity']['fresh']
    flow.now += timedelta(seconds=3)
    assert flow.store.status()['equity']['fresh'] is False
    observation['observed_at'] = flow.clock()
    with pytest.raises(StateError, match='stale_equity_quote'):
        flow.store.mark_equity(observation)
    assert flow.reconcile()
    assert flow.store.status()['equity']['fresh'] is False
    with pytest.raises(StateError, match='revision_mismatch'):
        flow.store.mark_equity(observation)


def test_repeated_drawdown_marks_preserve_coherence_peak_and_permanent_kill(flow):
    flow.open()
    peak = flow.store.mark_equity(observe(flow, value='250000000'))
    for _ in range(2):
        marked = flow.store.mark_equity(observe(flow, value='30000000'))
        status = flow.store.status()
        assert marked['peak_lamports'] == peak['peak_lamports']
        assert marked['max_drawdown'] == marked['current_drawdown']
        assert status['kill_reason'] == 'max_drawdown_15pct'
        assert status['reconciliation_required'] is False
        assert status['equity']['fresh'] and status['new_buys_enabled'] is False


def test_equity_status_also_expires_original_cash_observation(flow):
    observed = observe(flow)
    with flow.store.transaction() as c:
        flow.store.set_meta(c, 'reconciled_at', (flow.now - timedelta(seconds=10)).isoformat())
    flow.store.mark_equity(observed)
    flow.now += timedelta(seconds=6)
    assert flow.store.status()['equity']['fresh'] is False


def test_positive_equity_commit_rechecks_wall_age_after_shared_reader_wait(flow, monkeypatch):
    import sqlite3
    import threading
    from execution_state import LedgerConnection
    entered = threading.Event()
    original_commit = LedgerConnection.commit
    def commit(c):
        if c.commit_guards:
            entered.set()
        return original_commit(c)
    observed = {'wallet': 'wallet', 'balance_lamports': str(flow.base), 'observed_at': flow.clock(),
                'marks': {}, 'reconciliation_revision': flow.store.status()['reconciliation_revision']}
    reader = sqlite3.connect(flow.store.path, isolation_level=None)
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM meta').fetchall()
    monkeypatch.setattr(LedgerConnection, 'commit', commit)
    outcomes = []
    def mark():
        try: outcomes.append(flow.store.mark_equity(observed))
        except Exception as error: outcomes.append(error)
    worker = threading.Thread(target=mark)
    worker.start()
    try:
        assert entered.wait(2)
        flow.now += timedelta(seconds=16)
        worker.join(2)
        assert not worker.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], StateError)
        assert 'stale_equity_observation' in str(outcomes[0])
    finally:
        reader.rollback(); reader.close(); worker.join(2)
    assert flow.store.status()['equity'] is None


def test_negative_equity_safety_commit_is_not_cancelled_by_monitor_stop_guard(flow):
    flow.open()
    observed = observe(flow, value='1', costs='2000000000')
    def stopped(): raise StateError('monitor_stopped')
    assert flow.store.mark_equity(observed, read_guard=stopped) is None
    status = flow.store.status()
    assert status['equity_invalid']['reason'] == 'negative_liquidation_equity'
    assert status['kill_reason'] == 'max_drawdown_15pct'
    assert status['new_buys_enabled'] is False
