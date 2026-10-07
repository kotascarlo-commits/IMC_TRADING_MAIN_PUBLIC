"""Bounded read-only RPC fixtures; no network or wallet credentials."""
import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
import execution_evidence as evidence
import rpc_read_worker
from execution_state import StateError, load_config
from test_execution import flow
from test_execution import bridge


def transport(monkeypatch, replies):
    calls = []

    def request_once(self, payload, deadline):
        calls.append((payload, min(self.cfg['rpc_timeout_seconds'], deadline - time.monotonic())))
        value = replies.pop(0)
        return json.dumps({'jsonrpc': '2.0', 'id': 1, **value}).encode()

    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request_once)
    return calls


def test_context_not_ready_has_one_retry_and_preserves_minimum_slot(monkeypatch):
    calls = transport(monkeypatch, [{'error': {'code': -32016, 'message': 'SECRET'}}, {'result': {'value': 1}}])
    rpc = evidence.SolanaEvidence(load_config())
    params = ['wallet', {'commitment': 'finalized', 'minContextSlot': 123}]
    assert rpc.request('getBalance', params) == {'value': 1}
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[1][1] < calls[0][1]


@pytest.mark.parametrize('code', [-32005, -32602, 429, True, '-32016', None])
def test_other_errors_do_not_retry_or_disclose_messages(monkeypatch, code):
    calls = transport(monkeypatch, [{'error': {'code': code, 'message': 'SECRET'}}])
    with pytest.raises(evidence.RPCError) as error:
        evidence.SolanaEvidence(load_config()).request('getBalance', ['wallet'])
    assert len(calls) == 1
    assert error.value.code == (code if type(code) is int else None)
    assert 'SECRET' not in str(error.value)


def test_retry_exhaustion_retains_numeric_code(monkeypatch):
    calls = transport(monkeypatch, [{'error': {'code': -32016}}, {'error': {'code': -32016}}])
    with pytest.raises(evidence.RPCError) as error:
        evidence.SolanaEvidence(load_config()).request('getBalance', ['wallet'])
    assert len(calls) == 2 and error.value.code == -32016


def test_late_response_cannot_pass_deadline(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])

    def late(*args, **kwargs):
        clock[0] = 16.0
        return b'{"jsonrpc":"2.0","id":1,"result":1}'

    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', late)
    with pytest.raises(StateError, match='rpc_read_deadline'):
        evidence.SolanaEvidence(load_config()).request('getBalance', ['wallet'])


def test_decoding_cannot_cross_deadline(monkeypatch):
    clock = [10.0]
    rpc = evidence.SolanaEvidence(load_config())
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', lambda *a, **k: b'{}')

    def decode(body):
        clock[0] = 15.01
        return {'jsonrpc': '2.0', 'id': 1, 'result': 1}

    monkeypatch.setattr(evidence.json, 'loads', decode)
    with pytest.raises(StateError, match='rpc_read_deadline'):
        rpc.request('getBalance', ['wallet'])


def test_worker_only_returns_numeric_error_code(monkeypatch):
    monkeypatch.setattr(rpc_read_worker.urllib.request, 'urlopen', lambda *a, **k:
        io.BytesIO(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32016,"message":"SECRET"}}'))
    value = rpc_read_worker.read({'url': 'http://fixture', 'timeout': 1,
        'payload': {'jsonrpc': '2.0', 'id': 1, 'method': 'getBalance', 'params': []}})
    assert json.loads(value) == {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32016}}
    assert b'SECRET' not in value


def test_worker_rejects_broadcast_method_before_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('network must not be reached')
    monkeypatch.setattr(rpc_read_worker.urllib.request, 'urlopen', forbidden)
    with pytest.raises(ValueError, match='read_only_rpc_required'):
        rpc_read_worker.read({'payload': {'jsonrpc': '2.0', 'id': 1, 'method': 'sendTransaction', 'params': []}})


@pytest.mark.parametrize('consumer', ['order', 'reconciliation', 'bridge'])
def test_all_gmgn_reads_pin_absolute_repository_shim(monkeypatch, consumer):
    from types import SimpleNamespace
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'wallets': [
            {'chain': 'sol', 'address': 'wallet', 'balances': [{'symbol': 'SOL', 'balance': '1'}]}]}))

    monkeypatch.setattr(evidence.subprocess, 'run', run)
    if consumer == 'order':
        evidence.GMGNOrderEvidence(load_config()).request('order')
    elif consumer == 'reconciliation':
        evidence.ReconciliationReader(load_config()).request(['portfolio', 'info'])
    else:
        bridge.gmgn_sol_wallet()
    assert len(calls) == 1
    assert Path(calls[0][0]).is_absolute()
    assert Path(calls[0][0]) == Path(__file__).parents[1] / 'TOOLS/GMGN_READ_SHIM/gmgn-cli.cmd'


def test_nested_budget_never_extends_and_restores_on_failure():
    rpc = evidence.SolanaEvidence(load_config())
    with evidence.read_budget(rpc, 1):
        deadline = rpc.read_deadline
        with pytest.raises(RuntimeError):
            with evidence.read_budget(rpc, 15):
                assert rpc.read_deadline == deadline
                raise RuntimeError()
        assert rpc.read_deadline == deadline
    assert not hasattr(rpc, 'read_deadline')


def test_expired_budget_sends_nothing(monkeypatch):
    calls = transport(monkeypatch, [])
    rpc = evidence.SolanaEvidence(load_config())
    rpc.read_deadline = time.monotonic() - 1
    with pytest.raises(StateError, match='rpc_read_deadline'):
        rpc.request('getBalance', ['wallet'])
    assert not calls


@pytest.mark.parametrize('body,reason', [
    (b'{"jsonrpc":"2.0","id":true,"result":1}', 'rpc_envelope_invalid'),
    (b'x' * (4 * 1024 * 1024 + 1), 'rpc_response_too_large'),
], ids=['invalid-envelope', 'oversized-response'])
def test_bad_response_is_rejected(monkeypatch, body, reason):
    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', lambda *a, **k: body)
    with pytest.raises(StateError, match=reason):
        evidence.SolanaEvidence(load_config()).request('getBalance', ['wallet'])


@pytest.mark.parametrize('mode', ['headers', 'chunk-header', 'chunk-trailer'])
def test_real_worker_cancels_slow_http_and_is_reaped(monkeypatch, mode):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if mode == 'headers':
                    self.connection.sendall(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                else:
                    self.connection.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n')
                    if mode == 'chunk-trailer':
                        self.connection.sendall(b'0\r\nX-Slow: ')
                # Each byte arrives within the socket inactivity timeout.
                for _ in range(100):
                    self.connection.sendall(b'a')
                    time.sleep(.03)
            except OSError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    cfg = load_config()
    cfg.update(rpc_url=f'http://127.0.0.1:{server.server_port}', rpc_timeout_seconds=.6)
    children, original = [], evidence.subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(evidence.subprocess, 'Popen', start)
    started = time.monotonic()
    try:
        with pytest.raises(StateError, match='rpc_read_deadline'):
            evidence.SolanaEvidence(cfg).request('getBalance', ['wallet'])
        assert time.monotonic() - started < 2.5
        assert len(children) == 1 and children[0].poll() is not None
        assert Path(children[0].args[0]).resolve() == Path(sys._base_executable).resolve()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_snapshot_retries_only_valid_drift_and_discards_previous_inventory():
    slots = iter([42, 43, 44, 44, 45, 46, 46, 46, 46])
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        slot = next(slots)
        if method == 'getBalance':
            return {'context': {'slot': slot}, 'value': 1000}
        value = []
        if len(calls) == 1:
            value = [{'pubkey': 'account', 'account': {'owner': params[1]['programId'], 'executable': False,
                'data': {'parsed': {'type': 'account', 'info': {'owner': 'wallet', 'mint': 'old-mint',
                    'tokenAmount': {'decimals': 3, 'amount': '5'}}}}}}]
        return {'context': {'slot': slot}, 'value': value}

    reader = evidence.SolanaEvidence(load_config(), rpc)
    assert reader.wallet_snapshot('wallet') == ({}, {}, '1000', 46)
    assert len(calls) == 9
    assert calls[3][1][2]['minContextSlot'] == 44
    assert not hasattr(reader, 'read_deadline')


def test_invalid_balance_with_divergent_slots_is_never_retried():
    calls = []

    def rpc(method, params):
        calls.append(method)
        return {'context': {'slot': 43 if method == 'getBalance' else 42},
                'value': True if method == 'getBalance' else []}

    with pytest.raises(StateError, match='rpc_u64_required'):
        evidence.SolanaEvidence(load_config(), rpc).wallet_snapshot('wallet')
    assert len(calls) == 3


def test_snapshot_always_drifting_is_bounded():
    calls = []

    def rpc(method, params):
        calls.append(method)
        return {'context': {'slot': 40 + len(calls)}, 'value': 1000 if method == 'getBalance' else []}

    with pytest.raises(StateError, match='inconsistent_chain_snapshot_slots'):
        evidence.SolanaEvidence(load_config(), rpc).wallet_snapshot('wallet')
    assert len(calls) == 9


def group_reply(codes=None, **changes):
    results = [{'context': {'slot': 42}, 'value': []}, {'context': {'slot': 42}, 'value': []},
               {'context': {'slot': 42}, 'value': 1000}]
    replies = [{'jsonrpc': '2.0', 'id': index + 1, 'result': value} for index, value in enumerate(results)]
    for index, code in enumerate(codes or []):
        if code is not None:
            replies[index] = {'jsonrpc': '2.0', 'id': index + 1, 'error': {'code': code}}
    for index, value in changes.items():
        replies[int(index)] = value
    return json.dumps({'snapshot_responses': replies}).encode()


def test_group_context_retry_preserves_deadline_and_advances_validated_floor(monkeypatch):
    bodies = [group_reply([-32016]), group_reply()]
    calls = []

    def request(self, payload, deadline):
        calls.append((payload, deadline))
        return bodies.pop(0)

    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    rpc = evidence.SolanaEvidence(load_config())
    assert rpc.wallet_snapshot('wallet') == ({}, {}, '1000', 42)
    assert len(calls) == 2 and calls[0][1] == calls[1][1]
    assert calls[0][0] == {'operation': 'wallet_snapshot', 'wallet': 'wallet', 'minimum_slot': 0}
    assert calls[1][0] == {'operation': 'wallet_snapshot', 'wallet': 'wallet', 'minimum_slot': 42}


@pytest.mark.parametrize('kind', ['mixed-errors', 'non-list-inventory', 'wrong-id', 'both-result-error', 'invalid-balance', 'balance-below-floor'])
def test_group_invalid_or_mixed_evidence_never_retries(monkeypatch, kind):
    replies = json.loads(group_reply([-32016]))['snapshot_responses']
    if kind == 'mixed-errors':
        replies[1] = {'jsonrpc': '2.0', 'id': 2, 'error': {'code': -32005}}
    elif kind == 'non-list-inventory':
        replies[1]['result']['value'] = {}
    elif kind == 'wrong-id':
        replies[1]['id'] = 1
    elif kind == 'both-result-error':
        replies[1]['error'] = {'code': -32016}
    elif kind == 'invalid-balance':
        replies[2]['result']['value'] = True
    elif kind == 'balance-below-floor':
        replies[2]['result']['context']['slot'] = 41
    calls = []

    def request(self, payload, deadline):
        calls.append(payload)
        return json.dumps({'snapshot_responses': replies}).encode()

    monkeypatch.setattr(evidence.SolanaEvidence, '_request_once', request)
    with pytest.raises(StateError):
        evidence.SolanaEvidence(load_config()).wallet_snapshot('wallet')
    assert len(calls) == 1


def test_final_inventory_processing_cannot_cross_budget(monkeypatch):
    clock = [0.0]
    rpc = evidence.SolanaEvidence(load_config(), lambda *a: None)
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])

    def late(*args):
        clock[0] = 16
        return {}, {}, '1000', [42, 42, 42]

    monkeypatch.setattr(rpc, '_wallet_snapshot_round', late)
    with pytest.raises(StateError, match='rpc_read_deadline'):
        rpc.wallet_snapshot('wallet')


def test_wallet_snapshot_uses_at_least_persisted_fill_slot(flow):
    flow.open()
    calls = []

    def rpc(method, params):
        calls.append(params)
        assert params[2]['minContextSlot'] == 42
        raise StateError('isolated_slot_probe')

    reader = evidence.ReconciliationReader(load_config(),
        cli=lambda args: {'wallets': [{'chain': 'sol', 'address': 'wallet'}]},
        rpc=evidence.SolanaEvidence(load_config(), rpc), clock=flow.clock)
    with pytest.raises(StateError, match='isolated_slot_probe'):
        reader.snapshot(flow.store)
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['older-snapshot', 'expired-budget'])
def test_late_recovered_fill_is_not_applied_before_fresh_snapshot(flow, monkeypatch, failure):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    clock = [0.0]
    monkeypatch.setattr(evidence.time, 'monotonic', lambda: clock[0])

    class RPC:
        def wallet_snapshot(self, wallet):
            return {'mint': '1000'}, {'mint': 3}, '950000000', 41 if failure == 'older-snapshot' else 42

        def fill(self, event, signature):
            if failure == 'expired-budget':
                clock[0] = 16
            return dict(proof)

    def cli(args):
        if args[:2] == ['portfolio', 'info']:
            return {'wallets': [{'chain': 'sol', 'address': 'wallet'}]}
        if args[:2] == ['portfolio', 'holdings']:
            return {'list': [{'token': {'token_address': 'mint'}, 'balance': '1'}], 'next': ''}
        return {'order_id': data['order_id'], 'hash': data['signature'], 'status': 'confirmed', 'state': 30,
                'report': {'input_token': evidence.SOL, 'output_token': 'mint', 'input_amount': '50000000', 'output_amount': '1000'}}

    with pytest.raises(StateError, match='wallet_snapshot_precedes_fill|reconciliation_read_deadline'):
        evidence.ReconciliationReader(load_config(), cli=cli, rpc=RPC(), clock=flow.clock).snapshot(flow.store)
    assert flow.trade(event)['buy_raw'] == '0'


@pytest.mark.parametrize('slow_request', [None, 2, 3, 'disconnect'])
def test_snapshot_worker_reuses_connection_and_cancels_later_reads(monkeypatch, slow_request):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.client_address, request))
            try:
                if slow_request == 'disconnect' and request['id'] == 2:
                    self.close_connection = True
                    self.connection.close()
                    return
                if request['id'] == slow_request:
                    self.connection.sendall(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                    for _ in range(100):
                        self.connection.sendall(b'a')
                        time.sleep(.03)
                    self.close_connection = True
                    return
                result = {'context': {'slot': 42}, 'value': 1000 if request['method'] == 'getBalance' else []}
                body = json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    cfg = load_config()
    cfg.update(rpc_url=f'http://127.0.0.1:{server.server_port}', rpc_timeout_seconds=.6,
               reconciliation_max_age_seconds=1.2)
    children, original = [], evidence.subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(evidence.subprocess, 'Popen', start)
    try:
        if slow_request:
            with pytest.raises(StateError, match='rpc_read_deadline|rpc_worker_response_invalid'):
                evidence.SolanaEvidence(cfg).wallet_snapshot('wallet')
        else:
            assert evidence.SolanaEvidence(cfg).wallet_snapshot('wallet') == ({}, {}, '1000', 42)
            assert calls[2][1]['params'][1]['minContextSlot'] == 42
        assert len(children) == 1 and children[0].poll() is not None
        assert len({address for address, request in calls}) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
