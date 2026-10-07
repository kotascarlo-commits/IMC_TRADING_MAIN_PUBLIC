"""Dashboard fixtures never start or stop a real IMC process."""
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mykonos_meme_scout import dashboard

spec = importlib.util.spec_from_file_location('imc_operations_dashboard',
    Path(__file__).parents[1] / 'scripts/dashboard.py')
operations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operations)


def test_core_process_status_is_independent_of_data_freshness(monkeypatch):
    monkeypatch.setattr(operations, 'snapshot_detail', lambda:'Snapshot frisch')
    monkeypatch.setattr(operations, 'process_snapshot', lambda:[])
    assert operations.core_status()[0] is False
    monkeypatch.setattr(operations, 'process_snapshot', lambda:[{'Role':'CoreWrapper','ProcessId':42}])
    assert operations.core_status()[0] is False
    monkeypatch.setattr(operations, 'process_snapshot', lambda:[{'Role':'Core','ProcessId':43}])
    assert operations.core_status()[0] is True
    def unknown():
        raise RuntimeError('fixture CIM denied')
    monkeypatch.setattr(operations, 'process_snapshot', unknown)
    assert operations.core_status()[0] is None


def test_core_stop_uses_exact_common_identity_and_surfaces_failure(monkeypatch):
    commands = []
    class Result:
        returncode = 1
    monkeypatch.setattr(operations, 'process_command', lambda command:commands.append(command) or Result())
    with pytest.raises(RuntimeError, match='Stop nicht bestaetigt'):
        operations.stop_core()
    assert commands == ["Stop-IMCRoles -Roles @('CoreWrapper','Core')"]


def test_core_start_rechecks_wrapper_and_refuses_unknown_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No real process may be launched')
    monkeypatch.setattr(operations.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(operations, 'process_snapshot', lambda:[{'Role':'CoreWrapper'}])
    operations.start_core()
    def unknown():
        raise RuntimeError('UNKNOWN')
    monkeypatch.setattr(operations, 'process_snapshot', unknown)
    with pytest.raises(RuntimeError):
        operations.start_core()


def test_dashboard_csrf_and_action_failures_are_visible(monkeypatch):
    calls = []
    def fail_start():
        calls.append('start')
        raise RuntimeError('fixture start failed')
    monkeypatch.setattr(operations, 'start_core', fail_start)
    server = operations.HTTPServer(('127.0.0.1',0),operations.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port) + '/start'
    def post(body):
        request = urllib.request.Request(url, data=body, method='POST')
        with pytest.raises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request, timeout=2)
        return result.value.code
    try:
        assert post(b'csrf=wrong') == 403
        assert calls == []
        assert post(('csrf=' + operations.CSRF_TOKEN).encode()) == 503
        assert calls == ['start']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_execution_unavailable_never_returns_success_statistics(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('fixture offline')
    monkeypatch.setattr(dashboard.urllib.request,'urlopen',unavailable)
    status = dashboard.execution_status()
    assert status['available'] is False
    assert status['execution_mode'] == 'UNAVAILABLE'
    assert 'performance' not in status


def test_execution_endpoint_requires_schema_and_evidence_source(monkeypatch):
    class Response:
        doc = {'version':'imc-execution-ledger', 'new_buys_enabled':False, 'signals_enabled':True,
            'reconciliation_required':True, 'wallet_access':False, 'signing':False, 'broadcast':False,
            'open_positions':0, 'trades':[], 'events':[], 'kill_reasons':[],
            'performance':{'source':'fixture.db:trades/receipts/audit', 'unit':'lamports', 'count':0,
            'evidence_class':'FINALIZED_CHAIN_RECONCILED'}}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,limit): return json.dumps(self.doc).encode()
    monkeypatch.setattr(dashboard.urllib.request,'urlopen',lambda *args,**kwargs:Response())
    assert dashboard.execution_status()['available'] is True
    Response.doc['performance']['evidence_class'] = 'PAPER'
    assert dashboard.execution_status()['available'] is False


def test_dashboard_execution_labels_remain_separate_from_paper():
    assert 'Paper Cash' in dashboard.HTML
    assert 'bestätigte Blockchain-Daten' in dashboard.HTML
    assert 'Keine abgeschlossenen Trades' in dashboard.HTML
    assert 'DD: nicht verfügbar' in dashboard.HTML


def test_malformed_process_snapshot_is_unknown(monkeypatch):
    class Result:
        returncode = 0
        stdout = '[]'
    monkeypatch.setattr(operations,'process_command',lambda _:Result())
    monkeypatch.setattr(operations,'snapshot_detail',lambda:'fixture')
    assert operations.core_status()[0] is None
    Result.stdout = '{"UnknownInterpreterPids":[],"Processes":[{"Role":"Core","ProcessId":true}]}'
    assert operations.core_status()[0] is None
