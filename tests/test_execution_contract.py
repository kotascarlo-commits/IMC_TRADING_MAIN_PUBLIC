"""Regression contracts; fixture transport has no credentials or broadcast."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_execution import flow
from execution_state import Ledger, StateError


@pytest.mark.parametrize('target', ['POSITION_OPEN', 'CLOSED', 'SELL_SENT', 'BUY_ACK', 'UNRECOGNIZED'])
def test_illegal_transition_is_atomic(flow, target):
    tid = flow.candidate()
    with flow.store.transaction() as c:
        before = c.execute('SELECT count(*) FROM audit').fetchone()[0]
    with pytest.raises(StateError, match='illegal_state_transition'):
        with flow.store.transaction() as c:
            flow.store.transition(c, flow.store.get(c, 'trades', tid), target, 'fixture')
    with flow.store.transaction() as c:
        assert flow.store.get(c, 'trades', tid)['state'] == 'CANDIDATE'
        assert c.execute('SELECT count(*) FROM audit').fetchone()[0] == before


def test_transition_cannot_forge_source(flow):
    tid = flow.candidate()
    with pytest.raises(StateError, match='transition_source_mismatch'):
        with flow.store.transaction() as c:
            trade = flow.store.get(c, 'trades', tid)
            trade['state'] = 'RECONCILIATION_REQUIRED'
            flow.store.transition(c, trade, 'CLOSED', 'fixture')


def test_rejected_unsent_sell_can_exit_with_distinct_persistent_request(flow):
    buy = flow.open()
    first = flow.store.prepare_sell(buy['trade_id'])
    assert flow.store.feedback(flow.data(first, 'REJECTED'))
    assert flow.store.prepare_sell(buy['trade_id'], reason='recovery_exit') is None
    assert flow.store.status()['kill_reason'] == 'sell_rejected'
    assert flow.reconcile() is False  # Independent inventory proof, persistent kill.
    second = flow.store.prepare_sell(buy['trade_id'], reason='recovery_exit')
    assert second['event_id'] != first['event_id']
    assert second['request_id'] != first['request_id']
    assert second['trade_id'] == first['trade_id']
    assert second['amount_raw'] == '1000'
    assert flow.store.prepare_sell(buy['trade_id']) == second
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    # Recovery cannot reset the kill switch; it can still manage a known exit.
    flow.store.recover()
    with pytest.raises(StateError, match='sell_without_current_reconciliation'):
        flow.claim(second)
    with flow.store.transaction() as c:
        stored = flow.store.get(c, 'events', second['event_id'])
        assert stored['state'] == 'READY' and stored['request_id'] == second['request_id']
        assert stored.get('sent_at') is None
    assert flow.store.status()['kill_reason'] == 'sell_rejected'
    assert flow.reconcile() is False
    claimed = flow.claim(second)
    assert flow.store.feedback(*flow.fill(claimed))
    assert flow.reconcile() is False  # sell_rejected remains an explicit kill
    assert flow.trade(claimed)['state'] == 'CLOSED'
    assert flow.trade(claimed)['remaining_raw'] == '0'


def test_unknown_sell_never_creates_another_attempt(flow):
    buy = flow.open()
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(flow.data(sell, 'TIMEOUT'))
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    assert flow.store.prepare_sell(buy['trade_id']) is None
    with flow.store.transaction() as c:
        assert len([e for e in flow.store.all(c, 'events') if e['side'] == 'SELL']) == 1
        assert flow.store.get(c, 'events', sell['event_id'])['request_id'] == sell['request_id']


def test_claim_rechecks_session_limit_before_any_fill(flow):
    flow.store.cfg.update(max_open_positions=4, max_session_trades=1)
    first = flow.buy()
    second = flow.buy(mint='other')
    flow.claim(first)
    with pytest.raises(StateError, match='max_session_trades'):
        flow.claim(second)
    assert flow.trade(second)['state'] == 'APPROVED'


def test_parallel_claims_reserve_capital_transactionally(flow):
    flow.store.cfg.update(max_open_positions=4, position_fraction='0.60')
    events = [flow.buy(), flow.buy(mint='other')]
    def send(event):
        try:
            flow.claim(event, input_raw='600000000')
            return 'claimed'
        except StateError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, events)) == ['capital_limit', 'claimed']


def test_claim_rechecks_changed_position_limit(flow):
    flow.store.cfg['max_open_positions'] = 2
    first = flow.buy()
    second = flow.buy(mint='other')
    flow.store.cfg['max_open_positions'] = 1
    with pytest.raises(StateError, match='max_open_positions'):
        flow.claim(first)
    assert flow.trade(first)['state'] == flow.trade(second)['state'] == 'APPROVED'


def test_feedback_cannot_invent_a_send(flow):
    event = flow.buy()
    with pytest.raises(StateError, match='unknown_feedback_without_send'):
        flow.store.feedback(flow.data(event, 'TIMEOUT'))
    assert flow.trade(event)['state'] == 'APPROVED'


def test_session_reset_does_not_adopt_old_candidate(flow):
    candidate = flow.candidate()
    assert flow.store.control('reset_session')
    assert flow.store.prepare_buy(candidate) is None
    assert flow.store.status()['trades'][0]['state'] == 'REJECTED'


def test_claim_rejects_old_session_even_with_prepared_event(flow):
    event = flow.buy()
    with flow.store.transaction() as c:
        flow.store.set_meta(c, 'session', 'new-session-fixture')
    with pytest.raises(StateError, match='candidate_from_previous_session'):
        flow.claim(event)
