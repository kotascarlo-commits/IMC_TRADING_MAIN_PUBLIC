"""Health fixtures never start services and use only isolated SQLite files."""
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows health singleton')
if sys.platform == 'win32':
    spec = importlib.util.spec_from_file_location('imc_health', Path(__file__).parents[1] / 'RUNTIME/imc_system_health.py')
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)


@pytest.fixture
def report_fixture(monkeypatch, tmp_path):
    master = tmp_path / 'master.json'
    master.write_text(json.dumps({'mode': 'PURE_SIGNAL_ONLY', 'live_broadcast': False,
                                 'signing': False, 'wallet_access': False}))
    monkeypatch.setattr(health, 'MASTER_MODE', master)
    snapshot = {'processes': [{'Role': r, 'ProcessId': i} for i, r in enumerate(
        ['Core', 'Watcher', 'PureSignalGateway', 'Bridge'], 1)],
        'unknown_interpreter_pids': [], 'visibility': 'RECOGNIZED_ABSOLUTE_PATHS', 'error': None}
    monkeypatch.setattr(health, 'process_snapshot', lambda _: snapshot)
    monkeypatch.setattr(health, 'parse_status', lambda *args: {
        'exists': True, 'stale': False, 'last_error': None, 'saved_live_execution': None})
    monkeypatch.setattr(health, 'db_probe', lambda *args: {
        'readable': True, 'error': None, 'snapshots_stale': False, 'decisions_stale': False})
    monkeypatch.setattr(health, 'rate_snapshot', lambda: {'known': True, 'budgets': {}, 'error': None})
    monkeypatch.setattr(health, 'scan_status', lambda _: {'verified': True, 'error': None})
    return snapshot


def test_signal_only_does_not_require_offline_research_lanes(report_fixture):
    report = health.build_report()
    assert report['verdict'] == 'HEALTHY'
    assert set(report['statuses']) == {'safe100_watcher', 'pure_gateway', 'bridge'}
    assert set(report['databases']) == {'core', 'safe100_watcher'}
    assert report['live_execution_any'] is None
    assert report['authority_guard'] == 'UNKNOWN'


def test_data_only_health_requires_three_workers_and_preserves_source_mode(report_fixture, monkeypatch):
    monkeypatch.setenv('IMC_HEALTH_MODE','DATA_ONLY_SHADOW')
    report_fixture['processes']=[{'Role':r,'ProcessId':i} for i,r in enumerate(['Core','Watcher','Health'],1)]
    report=health.build_report()
    assert report['verdict']=='HEALTHY'
    assert report['configured_mode']=='DATA_ONLY_SHADOW' and report['source_master_mode']=='PURE_SIGNAL_ONLY'
    assert set(report['statuses'])=={'safe100_watcher'}
    report_fixture['processes'].append({'Role':'Bridge','ProcessId':99})
    assert health.build_report()['verdict']=='CRITICAL'


@pytest.mark.parametrize('fault',['status','database','master_flags'])
def test_data_only_missing_required_evidence_is_critical(report_fixture, monkeypatch,fault):
    monkeypatch.setenv('IMC_HEALTH_MODE','DATA_ONLY_SHADOW')
    report_fixture['processes']=[{'Role':r,'ProcessId':i} for i,r in enumerate(['Core','Watcher','Health'],1)]
    if fault=='status':
        monkeypatch.setattr(health,'parse_status',lambda *a: {'exists':False,'stale':None,'last_error':None,'saved_live_execution':None})
    elif fault=='database':
        monkeypatch.setattr(health,'db_probe',lambda *a: {'readable':False,'error':'fixture'})
    else:
        health.MASTER_MODE.write_text(json.dumps({'mode':'PURE_SIGNAL_ONLY','live_broadcast':True,'signing':False,'wallet_access':False}))
    assert health.build_report()['verdict']=='CRITICAL'


def test_data_only_empty_decisions_need_fresh_scan_but_normal_mode_still_blocks(report_fixture, monkeypatch):
    monkeypatch.setattr(health, 'db_probe', lambda *a: {
        'readable': True, 'error': None, 'snapshots_stale': False, 'decisions_stale': True})
    assert health.build_report()['verdict'] == 'CRITICAL'
    monkeypatch.setenv('IMC_HEALTH_MODE', 'DATA_ONLY_SHADOW')
    report_fixture['processes'] = [{'Role': r, 'ProcessId': i} for i, r in enumerate(['Core','Watcher','Health'],1)]
    report = health.build_report()
    assert report['verdict'] == 'DEGRADED' and not report['critical']
    monkeypatch.setattr(health, 'scan_status', lambda _: {'verified': False, 'error': 'fixture'})
    assert health.build_report()['verdict'] == 'CRITICAL'


@pytest.mark.parametrize('fault', ['valid', 'missing', 'stale', 'future', 'pid', 'pid_reuse',
    'schema', 'cycle', 'fractional_cycle', 'duration', 'bool_duration', 'evaluated', 'success', 'prebirth', 'naive'])
def test_scan_heartbeat_strict_schema_and_native_birth_binding(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(health, 'RUNTIME', tmp_path)
    now = datetime.now(timezone.utc)
    birth = str(int((now-timedelta(seconds=60)).timestamp()*10**7)+116444736000000000)
    value = {'schema_version': 1, 'pid': 42, 'process_birth_filetime': birth,
        'cycle_id': 1, 'cycle_seconds': .5, 'evaluated': 0, 'scan_success': True, 'updated_at': now.isoformat()}
    snapshot = {'processes': [{'Role': 'Core', 'ProcessId': 42, 'CreationFiletime': birth}]}
    if fault == 'stale': value['updated_at'] = (now-timedelta(seconds=31)).isoformat()
    elif fault == 'future': value['updated_at'] = (now+timedelta(seconds=2)).isoformat()
    elif fault == 'pid': value['pid'] = 43
    elif fault == 'pid_reuse': snapshot['processes'][0]['CreationFiletime'] = str(int(birth)+10000000)
    elif fault == 'schema': value['schema_version'] = True
    elif fault == 'cycle': value['cycle_id'] = 0
    elif fault == 'fractional_cycle': value['cycle_id'] = 1.5
    elif fault == 'duration': value['cycle_seconds'] = float('nan')
    elif fault == 'bool_duration': value['cycle_seconds'] = True
    elif fault == 'evaluated': value['evaluated'] = -1
    elif fault == 'success': value['scan_success'] = False
    elif fault == 'prebirth': value['updated_at'] = (now-timedelta(seconds=61)).isoformat()
    elif fault == 'naive': value['updated_at'] = now.replace(tzinfo=None).isoformat()
    if fault != 'missing': (tmp_path/'shadow-scan-status.json').write_text(json.dumps(value))
    assert health.scan_status(snapshot)['verified'] is (fault == 'valid')


def test_stale_mode_conflict_even_with_fresh_status(report_fixture):
    report_fixture['processes'].append({'Role': 'LiveSignalGateway', 'ProcessId': 99})
    report = health.build_report()
    assert report['verdict'] == 'CRITICAL'
    assert any('conflicts with configured mode' in s for s in report['critical'])


def test_wrapper_is_not_worker(report_fixture):
    report_fixture['processes'][0]['Role'] = 'CoreWrapper'
    assert any('Core: expected process not observed' in s for s in health.build_report()['critical'])


def test_partial_visibility_and_duplicates(report_fixture):
    report_fixture['unknown_interpreter_pids'] = [88]
    report_fixture['processes'].append({'Role': 'Watcher', 'ProcessId': 99})
    report = health.build_report()
    assert report['warnings']
    assert any('duplicate observed processes' in s for s in report['critical'])


def test_bad_config_is_not_healthy(monkeypatch, tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{}')
    monkeypatch.setattr(health, 'HEALTH_CONFIG', config)
    assert health.build_report()['verdict'] == 'DEGRADED'


def test_unknown_mode_is_not_healthy(report_fixture):
    health.MASTER_MODE.write_text(json.dumps({'mode': 'OLD_UNKNOWN'}))
    assert health.build_report()['verdict'] == 'DEGRADED'


@pytest.mark.parametrize('timestamp', ['invalid', '2026-01-01T12:00:00',
    (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()])
def test_invalid_timestamps_do_not_look_fresh(timestamp):
    assert health._age_seconds(timestamp) is None


def test_probe_requires_schema_and_does_not_create_missing_db(tmp_path):
    missing = tmp_path / 'missing.db'
    assert not health.db_probe(missing, 'snapshots', True, 30)['readable']
    assert not missing.exists()
    source = tmp_path / 'empty.db'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE unrelated(id INTEGER)')
    before = source.read_bytes()
    assert not health.db_probe(source, 'snapshots', True, 30)['readable']
    assert source.read_bytes() == before


def test_probe_uses_wal_and_separates_data_freshness(tmp_path):
    path = tmp_path / 'core.db'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    for table in ('snapshots', 'decisions'):
        writer.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,captured_at TEXT)')
        writer.execute(f'INSERT INTO {table} VALUES(1,?)', (datetime.now(timezone.utc).isoformat(),))
    writer.commit()
    try:
        before = [p.read_bytes() for p in (path, Path(str(path) + '-wal'))]
        result = health.db_probe(path, 'snapshots', True, 30)
        assert result['readable'] and not result['snapshots_stale'] and not result['decisions_stale']
        assert before == [p.read_bytes() for p in (path, Path(str(path) + '-wal'))]
    finally:
        writer.close()


def test_missing_rate_database_is_unknown_without_creation(monkeypatch, tmp_path):
    path = tmp_path / 'rate.db'
    monkeypatch.setattr(health, 'RATE_DB', path)
    assert health.rate_snapshot()['known'] is False
    assert not path.exists()


def test_process_timeout_reports_unknown(monkeypatch):
    def fail(*args, **kwargs):
        raise health.subprocess.TimeoutExpired('fixture', 1)
    monkeypatch.setattr(health.subprocess, 'run', fail)
    assert health.process_snapshot(1)['visibility'] == 'UNKNOWN'


def test_unknown_process_evidence_preserved_without_changing_guard(monkeypatch):
    import json
    from types import SimpleNamespace
    evidence = {'pid': 88, 'parent_pid': 22, 'reason': 'NO_COMMANDLINE',
                'metadata_gaps': ['NATIVE_IDENTITY_UNAVAILABLE'], 'parent': None}
    payload = {'processes': [], 'interpreter_redirectors': [],
               'unknown_interpreter_pids': [88], 'unknown_interpreter_processes': [evidence],
               'snapshot_at': '2026-10-06T20:00:00+00:00', 'snapshot_id': 'fixture',
               'expired_process_evidence': [{'pid': 89, 'decision_reason': 'ABSENT_AT_PROBE'}]}
    monkeypatch.setattr(health.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(payload)))
    result = health.process_snapshot(1)
    assert result['unknown_interpreter_pids'] == [88] and result['visibility'] == 'PARTIAL'
    assert result['unknown_interpreter_processes'] == [evidence]
    assert result['expired_process_evidence'] == payload['expired_process_evidence']
