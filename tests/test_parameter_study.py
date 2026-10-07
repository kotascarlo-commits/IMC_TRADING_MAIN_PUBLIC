"""Causality, loss accounting and selection safeguards for offline research."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('imc_parameter_study', ROOT/'RESEARCH/parameter_study.py')
study = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = study
spec.loader.exec_module(study)


def event(t=0):
    return {'id': 1, 't': t, 'mint': 'token', 'liq': 200000,
            'buy': .9, 'momentum': 25, 'age': 2, 'score': 90,
            'volume_ratio': .05, 'strict_static': ['security:creator_holder_pct'],
            'diagnostic_static': []}


def path(rows):
    return [[t, p, 200000, True, i] for i, (t, p) in enumerate(rows)]


def params(**kw):
    return study.Parameters('fixture', hold=kw.pop('hold', 4), **kw)


def test_sqlite_source_is_readonly(tmp_path):
    source = tmp_path/'source.db'
    with sqlite3.connect(source) as c:
        c.execute('create table rows(x)')
    c = study.readonly(source)
    with pytest.raises(sqlite3.OperationalError):
        c.execute('insert into rows values(1)')
    c.close()


def test_no_backdated_entry_or_interpolation():
    p = path([(0, 1), (1, 1), (3, 2), (5, 2), (7, 2), (9, 2)])
    t = study.simulate(event(), params(), p, 1000)
    assert t['entry_at'] == 3
    assert t['exit_at'] == 9
    assert t['pnl'] < 0  # A pre-delay cheap price must never create profit.


def test_missing_entry_is_not_a_profitable_trade():
    assert study.simulate(event(), params(), path([(0, 1), (11, 1)]), 1000) is None


def test_liquidity_is_rechecked_at_entry():
    p = path([(0, 1), (2, 1), (4, 1), (8, 1)])
    p[1][2] = 30000
    assert study.simulate(event(), params(), p, 1000) is None


def test_exit_delayed_and_stop_price_not_guaranteed():
    p = path([(2, 1), (3, .7), (5, .1), (8, .1)])
    t = study.simulate(event(), params(), p, 1000)
    assert t['reason'] == 'stop_loss'
    assert t['exit_at'] == 5
    assert t['pnl'] < -85


def test_missing_exit_is_full_loss():
    t = study.simulate(event(), params(), path([(2, 1), (4, 1), (6, 1)]), 1000)
    assert t['unvalued_total_loss']
    assert t['pnl'] == -t['invested']


def test_disappeared_token_not_dropped_from_denominator():
    m, trades = study.portfolio([event()], {'token': path([(2, 1)])}, params(), 0, 1000)
    assert m['trades'] == m['unvalued_total_losses'] == 1
    assert m['net_usd'] < -100


def test_large_gap_does_not_use_later_recovery_price():
    t = study.simulate(event(), params(hold=180), path([(2, 1), (80, 20), (190, 20)]), 1000)
    assert t['unvalued_total_loss']
    assert t['pnl'] < 0


def test_stale_observations_do_not_reset_freshness_clock():
    p = path([(2, 1), (20, 1), (40, 1), (45, 20), (190, 20)])
    p[1][3] = p[2][3] = False
    t = study.simulate(event(), params(hold=180), p, 1000)
    assert t['unvalued_total_loss']


def test_unliquidatable_position_is_total_loss():
    p = path([(2, 1), (3, 1), (5, 1)])
    p[1][2] = 0
    t = study.simulate(event(), params(), p, 1000)
    assert t['reason'] == 'unliquidatable_total_loss'
    assert t['pnl'] == -t['invested']


def test_single_position_and_one_token_per_split():
    e1 = event(); e2 = dict(event(3), id=2, mint='other'); e3 = dict(event(20), id=3)
    p = path([(2, 1), (4, 1), (6, 1), (8, 1), (22, 1), (26, 1), (28, 1)])
    m, trades = study.portfolio([e1, e2, e3], {'token': p, 'other': p}, params(), 0, 1000)
    assert m['trades'] == 1
    assert m['unique_tokens'] == 1


def test_purge_excludes_boundary_signals():
    m, _ = study.portfolio([event(599), event(600)], {'token': path([(602, 1)])}, params(), 0, 1000)
    assert m['trades'] == 1  # 599 is eligible but cannot fill before 609.
    assert study.PURGE > 300+2*study.DELAY+2*study.WAIT+study.GAP


def test_strict_security_does_not_inherit_diagnostic_permission():
    assert study.eligible(event(), params())
    assert not study.eligible(event(), params(), strict=True)


def test_costs_reduce_constant_price_return():
    p = path([(2, 1), (4, 1), (6, 1), (8, 1)])
    base = study.simulate(event(), params(), p, 1000, extra=0)
    stress = study.simulate(event(), params(), p, 1000, extra=.03)
    assert stress['pnl'] < base['pnl'] < 0


def test_drawdown_includes_open_position_marks():
    trades = [{'mint': 'a', 'entry_at': 0, 'pnl': 10,
               'marks': [(1, -100), (2, 10)], 'unvalued_total_loss': False}]
    assert study.metrics(trades)['drawdown_pct'] == 10


def test_drawdown_includes_sell_delay_before_recovery_fill():
    p = path([(2, 1), (4, 1), (6, 1), (7, .2), (8, 1)])
    trade = study.simulate(event(), params(), p, 1000)
    assert trade['reason'] == 'maximum_hold'
    assert trade['exit_at'] == 8
    assert any(at == 7 for at, _ in trade['marks'])
    # Price recovers before the fill; portfolio still experienced the drawdown.
    assert study.metrics([trade])['drawdown_pct'] > 8
    assert study.metrics([trade])['drawdown_pct'] > -trade['pnl']/10


def test_tiny_profitable_sample_cannot_be_selected():
    m = {'trades': 4, 'unique_tokens': 4, 'net_usd': 100, 'pf': 100}
    plan = {'minimum_trades_train': 30, 'minimum_trades_validation': 20,
            'minimum_unique_tokens_validation': 20}
    assert not study.admissible(m, m, plan)


def test_missing_independent_days_has_no_confidence_interval():
    trades = [{'entry_at': 0, 'pnl': 100}, {'entry_at': 1, 'pnl': 100}]
    assert study.bootstrap(trades)['status'] == 'INSUFFICIENT_INDEPENDENT_DAYS'


def snapshot(sec):
    t = study.epoch('2026-09-25T12:00:00+00:00')
    return {'address': 'So11111111111111111111111111111111111111112', 'symbol': 'T',
            'name': 'T', 'pair_address': 'pair', 'dex_id': 'dex', 'price_usd': 2,
            'liquidity_usd': 200000, 'market_cap_usd': 1000000,
            'volume_5m_usd': 10000, 'volume_1h_usd': 10000, 'buys_5m': 90,
            'sells_5m': 10, 'price_change_5m_pct': 25,
            'pair_created_at_ms': int((t-120)*1000),
            'captured_at': study.iso(t), 'market_data_stale': False, 'security': sec}


def feature(sec):
    v = snapshot(sec); previous = dict(v, captured_at=study.iso(study.epoch(v['captured_at'])-4), price_usd=1.99)
    cfg = {'maximum_top10_holder_pct': 30, 'maximum_creator_holder_pct': 5,
           'snapshot_max_age_seconds': 10, 'confirmation_max_gap_seconds': 30,
           'minimum_score': 0, 'minimum_liquidity_usd': 30000,
           'maximum_entry_age_minutes': 15, 'minimum_buy_ratio': .6,
           'minimum_momentum_pct': 5, 'maximum_momentum_pct': 120,
           'maximum_volume_liquidity_ratio': 1, 'liquidity_drain_pct': 15}
    return study.feature((1, v['address'], json.dumps(v)), previous, cfg)


def test_missing_holder_diagnostic_never_fabricates_evidence():
    f = feature(dict(study.REQUIRED_FLAGS))
    assert 'security:top10_holder_pct' in f['strict_static']
    assert 'security:creator_holder_pct' in f['strict_static']
    assert 'security_timestamp_unverified' in f['missing_evidence']
    assert not f['diagnostic_static']


def test_known_unsafe_holder_not_ignored():
    f = feature(dict(study.REQUIRED_FLAGS, top10_holder_pct=90, creator_holder_pct=50))
    assert 'security:top10_holder_pct' in f['diagnostic_static']
    assert 'security:creator_holder_pct' in f['diagnostic_static']


def test_other_security_unknown_remains_blocked():
    f = feature(dict(study.REQUIRED_FLAGS, malicious_creator=None))
    assert 'security:malicious_creator' in f['diagnostic_static']


def test_stale_security_not_ignored():
    f = feature(dict(study.REQUIRED_FLAGS, checked_at='2026-09-25T11:00:00+00:00'))
    assert 'security_timestamp_not_fresh' in f['diagnostic_static']


def test_internal_age_gate_remains_effective():
    v = snapshot(dict(study.REQUIRED_FLAGS))
    v['pair_created_at_ms'] -= 60*60*1000
    from mykonos_meme_scout.models import SecuritySignals, TokenSnapshot
    from mykonos_meme_scout.scoring import assess
    v['security'] = SecuritySignals(**v['security'])
    assert 'entry_window_closed' in assess(TokenSnapshot(**v), study.Config()).hard_failures


def test_grid_is_bounded_and_deterministic():
    profiles = study.grid()
    assert len(profiles) == 84
    assert len({p.name for p in profiles}) == len(profiles)
    assert profiles[0].liquidity == 100000
    assert profiles[0].fraction == .05


def test_future_prices_after_exit_cannot_change_a_trade():
    p = path([(2, 1), (4, 1), (6, 1), (8, 1)])
    before = study.simulate(event(), params(), p, 1000)
    after = study.simulate(event(), params(), p+path([(100, 100000)]), 1000)
    assert before == after


def test_code_change_invalidates_frozen_plan():
    with pytest.raises(ValueError, match='code changed'):
        study.verify_plan({'code_sha256': {'RESEARCH/parameter_study.py': '0'*64}})


def test_study_refuses_second_final_test(tmp_path):
    plan = {'code_sha256': {}}
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    (tmp_path/'dataset.json').write_text(json.dumps({'plan_hash': study.digest(plan)}))
    (tmp_path/'selection.json').write_text('{}')
    with pytest.raises(ValueError, match='single-use'):
        study.study(tmp_path)


def test_final_candidate_needs_independent_sample_and_stress():
    m = {'trades': 100, 'unique_tokens': 100, 'entry_days': 5, 'win_rate_pct': 75,
         'pf': 2.1, 'drawdown_pct': 10, 'top3_share_of_net_profit_pct': 20,
         'net_without_top3': 50}
    plan = {'minimum_trades_final_test': 30, 'minimum_test_days': 3}
    assert study.final_qualifies(m, {'mean_pnl_95pct': [1, 2]}, {'net_usd': 10}, plan)
    assert not study.final_qualifies(m, {'mean_pnl_95pct': [-1, 2]}, {'net_usd': 10}, plan)
    assert not study.final_qualifies(m, {'mean_pnl_95pct': [1, 2]}, {'net_usd': -10}, plan)
