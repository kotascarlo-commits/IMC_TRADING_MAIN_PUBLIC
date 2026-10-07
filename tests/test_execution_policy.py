"""The only ON values are isolated owner-gate fixtures, never project config."""
import json
import time

import pytest

from test_execution import flow
from execution_policy import RiskEngine, owner_gate, quality_gates, CRITICAL, CAPABILITIES


@pytest.mark.parametrize('contents', [None, '{}', 'null', '[]', '{',
    '{"schema_version":true,"OWNER_BROADCAST_GATE":"ON"}',
    '{"schema_version":1,"OWNER_BROADCAST_GATE":true}',
    '{"schema_version":1,"OWNER_BROADCAST_GATE":"ON","extra":1}',
    '{"schema_version":1,"OWNER_BROADCAST_GATE":"OFF","OWNER_BROADCAST_GATE":"ON"}',
    'x' * 1025])
def test_bad_owner_gate_is_off(tmp_path, contents):
    path = tmp_path / 'gate.json'
    if contents is not None:
        path.write_text(contents, encoding='utf-8')
    assert owner_gate(path) == {'value': 'OFF', 'valid': False}


@pytest.mark.parametrize('gate', ['OFF', 'ON'])
def test_exact_owner_gate_is_read_only(tmp_path, gate):
    path = tmp_path / 'gate.json'
    contents = json.dumps({'schema_version': 1, 'OWNER_BROADCAST_GATE': gate}).encode()
    path.write_bytes(contents)
    assert owner_gate(path) == {'value': gate, 'valid': True}
    assert path.read_bytes() == contents


def test_gate_on_without_verified_components_does_not_authorize(flow, tmp_path):
    path = tmp_path / 'gate.json'
    path.write_text('{"schema_version":1,"OWNER_BROADCAST_GATE":"ON"}')
    decision = RiskEngine(flow.store, gate_path=path, clock=flow.clock).assess(heartbeats={}, capabilities={})
    assert decision['owner_gate'] == 'ON'
    assert decision['ready'] is decision['authorized'] is False
    assert decision['mode'] == 'UNKNOWN'
    assert 'capability:buy_transport' in decision['failures']
    assert 'performance_gates' in decision['failures']
    with flow.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM audit WHERE action='risk_decision'").fetchone()[0] == 1


def test_quality_does_not_promote_missing_or_concentrated_results():
    performance = {'evidence_class': 'FINALIZED_CHAIN_RECONCILED', 'source': 'fixture:trades',
                   'count': 100, 'profit_factor': '2', 'win_rate': '.70', 'top3_net_profit_share': '.25'}
    equity = {'max_drawdown': '.15'}
    assert all(quality_gates(performance, equity).values())
    for key, value in [('count', 2), ('profit_factor', None), ('win_rate', '.699'),
                       ('top3_net_profit_share', '.251'), ('evidence_class', 'SIMULATION')]:
        assert not all(quality_gates(dict(performance, **{key: value}), equity).values())
    assert not all(quality_gates(performance, None).values())
    assert not all(quality_gates(performance, {'max_drawdown': '.1501'}).values())


def test_owner_off_blocks_verified_exit_without_performance_gate(flow, tmp_path):
    buy = flow.open()
    event = flow.store.prepare_sell(buy['trade_id'], reason='risk_exit')
    quote = dict(flow.quote(event), source='GMGN_ORDER_QUOTE')
    path = tmp_path / 'gate.json'
    path.write_text('{"schema_version":1,"OWNER_BROADCAST_GATE":"OFF"}')
    heartbeats = {component: {'status': 'PASS', 'source': 'isolated-fixture', 'observed_at': flow.clock(),
                             'valid_until_monotonic': time.monotonic()+2}
                  for component in CRITICAL}
    decision = RiskEngine(flow.store, gate_path=path, clock=flow.clock).assess(
        heartbeats=heartbeats, capabilities={name: True for name in CAPABILITIES},
        side='SELL', event=event, quote=quote)
    assert decision['ready'] is True
    assert decision['authorized'] is False
    assert decision['mode'] == 'ARMED_NO_BROADCAST'


def test_unknown_or_stale_heartbeat_blocks_exit_send(flow, tmp_path):
    buy = flow.open()
    event = flow.store.prepare_sell(buy['trade_id'])
    quote = dict(flow.quote(event), source='GMGN_ORDER_QUOTE')
    path = tmp_path / 'gate.json'
    path.write_text('{"schema_version":1,"OWNER_BROADCAST_GATE":"ON"}')
    heartbeats = {component: {'status': 'PASS', 'source': 'fixture', 'observed_at': flow.clock(),
                             'valid_until_monotonic': time.monotonic()+2}
                  for component in CRITICAL}
    heartbeats['RPC']['status'] = 'UNKNOWN'
    decision = RiskEngine(flow.store, gate_path=path, clock=flow.clock).assess(
        heartbeats=heartbeats, capabilities={name: True for name in CAPABILITIES},
        side='SELL', event=event, quote=quote)
    assert decision['authorized'] is False
    assert 'heartbeat:RPC' in decision['failures']


@pytest.mark.parametrize('deadline', [None, True, float('nan'), float('inf'), -1])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_original_pm_lease_is_required_even_with_fresh_wall_time(flow, tmp_path, monkeypatch, deadline, side):
    from test_execution_adapter import ready
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch, side=side)
    observed = probes(side)
    observed['heartbeats']['POSITION_MANAGER']['valid_until_monotonic'] = deadline
    result = risk.assess(**observed, side=side, event=event, quote=quote)
    assert not result['ready'] and not result['authorized']
    assert 'heartbeat:POSITION_MANAGER' in result['failures']


def test_pm_original_deadline_caps_permit_and_expiry_after_audit_revokes(flow, tmp_path, monkeypatch):
    from test_execution_adapter import ready
    from execution_adapter import build_swap_payload
    from execution_state import StateError
    event, quote, risk, probes = ready(flow, tmp_path, monkeypatch)
    claimed, lease = risk.reserve(event['event_id'], quote, build_swap_payload(event, quote, flow.store.cfg))
    observed = probes('BUY')
    deadline = time.monotonic() + .3
    observed['heartbeats']['POSITION_MANAGER']['valid_until_monotonic'] = deadline
    result = risk.assess(**observed, event=claimed, quote=quote, lease=lease)
    assert result['authorized']
    assert risk._approved[lease.event_id][4] <= deadline
    original = flow.store.audit
    def slow_audit(c, action, doc, tid=None):
        if action == 'risk_decision':
            time.sleep(.04)
        return original(c, action, doc, tid)
    monkeypatch.setattr(flow.store, 'audit', slow_audit)
    observed['heartbeats']['POSITION_MANAGER']['valid_until_monotonic'] = time.monotonic() + .03
    result = risk.assess(**observed, event=claimed, quote=quote, lease=lease)
    assert not result['ready'] and not result['authorized']
    assert 'evidence_expired_before_return' in result['failures']
    with pytest.raises(StateError, match='send_lease_not_authorized'):
        risk.authorize_send(lease, result)
