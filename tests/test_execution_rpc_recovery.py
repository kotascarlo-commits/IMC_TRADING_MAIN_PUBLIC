"""Bounded read recovery and safe diagnostics; entirely isolated transports."""
import io
import json
from types import SimpleNamespace
import urllib.error

import pytest

from test_execution_rpc import group_reply
from test_execution import flow
import execution_evidence as evidence
import rpc_read_worker as worker
from execution_state import StateError, load_config
from execution_monitor import ReadMonitor


def test_balance_lag_retries_only_validated_balance_with_fixed_floor(monkeypatch):
    calls = []
    def request(self, payload, deadline):
        calls.append((dict(payload), deadline))
        if payload.get('operation') == 'wallet_snapshot':
            assert len(calls) == 1
            return group_reply([None, None, -32016])
        assert payload['method'] == 'getBalance'
        assert payload['params'] == ['wallet', {'commitment': 'finalized', 'minContextSlot': 42}]
        return b'{"jsonrpc":"2.0","id":1,"result":{"context":{"slot":42},"value":1000}}'
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    assert evidence.SolanaEvidence(load_config()).wallet_snapshot('wallet') == ({}, {}, '1000', 42)
    assert len(calls) == 2 and calls[1][1] <= calls[0][1] - 3


@pytest.mark.parametrize('kind', ['token-drift', 'unknown-account', 'invalid-amount'])
def test_balance_is_never_retried_for_unverified_token_inventory(monkeypatch, kind):
    bodies = json.loads(group_reply([None, None, -32016]))
    token = bodies['snapshot_responses'][0]['result']
    if kind == 'token-drift': token['context']['slot'] = 41
    else:
        token['value'] = [{'pubkey': 'account', 'account': {'owner': 'foreign' if kind == 'unknown-account' else worker.PROGRAMS[0],
            'executable': False, 'data': {'parsed': {'type': 'account', 'info': {'owner': 'wallet', 'mint': 'mint',
                'tokenAmount': {'decimals': 3, 'amount': True if kind == 'invalid-amount' else '1'}}}}}}]
    calls = []
    def request(self, payload, deadline):
        calls.append(dict(payload))
        assert payload.get('operation') == 'wallet_snapshot', 'no balance retry on unverified tokens'
        if len(calls) == 1: return json.dumps(bodies).encode()
        return group_reply()
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    rpc = evidence.SolanaEvidence(load_config())
    if kind == 'token-drift': assert rpc.wallet_snapshot('wallet') == ({}, {}, '1000', 42)
    else:
        with pytest.raises(StateError): rpc.wallet_snapshot('wallet')
        assert len(calls) == 1


def test_lag_and_drift_have_one_shared_attempt_budget(monkeypatch):
    calls = []
    def request(self, payload, deadline):
        calls.append(dict(payload))
        if payload.get('method') == 'getBalance':
            return b'{"jsonrpc":"2.0","id":1,"error":{"code":-32016,"context_slot":41}}'
        return group_reply([None, None, -32016])
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    with pytest.raises(evidence.RPCError) as caught:
        evidence.SolanaEvidence(load_config()).wallet_snapshot('wallet')
    assert caught.value.code == -32016
    assert sum(p.get('operation') == 'wallet_snapshot' for p in calls) == 3
    assert sum(p.get('method') == 'getBalance' for p in calls) == 1
    assert len(calls) == 4  # at most 3 * 3 grouped reads plus 1 balance read
    assert calls[-1]['minimum_slot'] == 42


def test_later_balance_discards_all_previous_inventory_and_advances_floor(monkeypatch):
    calls = []
    def request(self, payload, deadline):
        calls.append(dict(payload))
        if len(calls) == 1: return group_reply([None, None, -32016])
        if payload.get('method') == 'getBalance':
            return b'{"jsonrpc":"2.0","id":1,"result":{"context":{"slot":43},"value":1000}}'
        assert payload['minimum_slot'] == 43
        value = json.loads(group_reply())
        for reply in value['snapshot_responses']: reply['result']['context']['slot'] = 43
        return json.dumps(value).encode()
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    assert evidence.SolanaEvidence(load_config()).wallet_snapshot('wallet') == ({}, {}, '1000', 43)
    assert len(calls) == 3


def test_worker_error_context_is_typed_and_secrets_are_projected_out():
    value = worker.envelope({'jsonrpc': '2.0', 'id': 1,
        'error': {'code': -32016, 'message': 'SECRET', 'data': {'contextSlot': 41, 'key': 'SECRET'}}}, 1)
    assert value == {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32016, 'context_slot': 41}}
    for slot in (True, -1, 2**64, '41'):
        with pytest.raises(ValueError):
            worker.envelope({'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32016, 'data': {'contextSlot': slot}}}, 1)


def test_worker_failure_never_serializes_provider_exception(monkeypatch):
    output = io.BytesIO()
    monkeypatch.setattr(worker.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}')))
    monkeypatch.setattr(worker.sys, 'stdout', SimpleNamespace(buffer=output))
    def failed(*args):
        raise urllib.error.HTTPError('https://fixture/?api_key=SECRET', 403, 'SECRET', {}, None)
    monkeypatch.setattr(worker, 'read', failed)
    assert worker.main() == 1
    assert json.loads(output.getvalue()) == {'worker_failure': {'kind': 'HTTP_STATUS', 'http_status': 403}}
    assert b'SECRET' not in output.getvalue() and b'https' not in output.getvalue()


@pytest.mark.parametrize('error', [evidence.RPCError(-32016, context_slot=41), evidence.RPCTransportError('HTTP_STATUS', 429)])
def test_monitor_persists_rpc_and_http_failures_as_distinct_typed_fields(flow, tmp_path, error):
    class Reader:
        def __init__(self, cfg): pass
        def snapshot(self, store, **kwargs): raise error
    monitor = ReadMonitor(flow.store, tmp_path / 'monitor.json', reader_factory=Reader)
    with pytest.raises(StateError): monitor.tick()
    projected = json.loads(monitor.status_path.read_text())
    if isinstance(error, evidence.RPCError):
        assert projected['rpc_code'] == -32016 and projected['rpc_context_slot'] == 41 and projected['http_status'] is None
    else:
        assert projected['http_status'] == 429 and projected['rpc_code'] is None
    with flow.store.transaction() as c:
        audit = json.loads(c.execute("SELECT doc FROM audit WHERE action='api_failure' ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert audit['details'] == {k: projected[k] for k in ('rpc_code', 'rpc_context_slot', 'http_status')}


def test_group_error_retains_highest_successful_balance_floor(monkeypatch):
    calls = []
    def request(self, payload, deadline):
        calls.append(dict(payload))
        if len(calls) == 1:
            body = json.loads(group_reply([-32016]))
            body['snapshot_responses'][2]['result']['context']['slot'] = 43
        else:
            assert payload['minimum_slot'] == 43
            body = json.loads(group_reply())
            for reply in body['snapshot_responses']: reply['result']['context']['slot'] = 43
        return json.dumps(body).encode()
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    assert evidence.SolanaEvidence(load_config()).wallet_snapshot('wallet') == ({}, {}, '1000', 43)


@pytest.mark.parametrize('second_context', [40, None])
def test_mixed_error_code_and_context_always_come_from_same_response(monkeypatch, second_context):
    body = json.loads(group_reply([-32016, -32005]))
    body['snapshot_responses'][0]['error']['context_slot'] = 41
    if second_context is not None: body['snapshot_responses'][1]['error']['context_slot'] = second_context
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', lambda *args: json.dumps(body).encode())
    with pytest.raises(evidence.RPCError) as caught:
        evidence.SolanaEvidence(load_config()).wallet_snapshot('wallet')
    assert caught.value.code == -32005 and caught.value.context_slot == second_context


def test_all_lag_and_drift_rounds_keep_application_time_reserve(monkeypatch):
    clock = [0.0]
    deadlines = []
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(evidence.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def request(self, payload, deadline):
        deadlines.append(deadline)
        if len(deadlines) == 1:
            clock[0] = 8
            return group_reply([None, None, -32016])
        if payload.get('method') == 'getBalance':
            clock[0] = 11.7
            return b'{"jsonrpc":"2.0","id":1,"error":{"code":-32016}}'
        clock[0] = 12.01
        return group_reply()
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    rpc = evidence.SolanaEvidence(load_config())
    rpc.read_deadline = 15
    with pytest.raises(StateError, match='rpc_read_deadline'):
        rpc.wallet_snapshot('wallet')
    assert all(deadline <= 12 for deadline in deadlines)
    assert rpc.read_deadline == 15
