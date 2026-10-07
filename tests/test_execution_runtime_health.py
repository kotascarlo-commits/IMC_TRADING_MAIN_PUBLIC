"""Generation/freshness contracts in isolated files; no production processes."""
import copy
import json
import os
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_execution import flow
from test_execution_monitor import monitor_for, wait_monitor_pass
import execution_monitor as monitor_module
from execution_state import StateError


@pytest.fixture
def runtime(flow, tmp_path, monkeypatch):
    root=tmp_path/'RUNTIME'
    root.mkdir()
    birth=(flow.now-timedelta(seconds=10)).isoformat()
    published=(flow.now-timedelta(seconds=1)).isoformat()
    mapping={'CORE':'Core','WATCHER':'Watcher','GATEWAY':'PureSignalGateway','BRIDGE':'Bridge','HEALTH':'Health'}
    rows=[{'Role':role,'ProcessId':os.getpid() if role=='PureSignalGateway' else i+100,
           'CreationDate':birth} for i,role in enumerate([*mapping.values(),'SignalSupervisor'])]
    native={'processes':rows,'unknown_interpreter_pids':[],'error':None}
    status={'state':'RUNNING','pid':rows[-1]['ProcessId'],'updated_at':published,
            'workers':{key:{'pid':rows[i]['ProcessId']} for i,key in enumerate(mapping)},
            'broadcast':False,'live_ready':False,'execution_mode':'SIGNAL_ONLY'}
    report={'updated':published,'configured_mode':'PURE_SIGNAL_ONLY','process_snapshot':copy.deepcopy(native),
            'verdict':'HEALTHY'}
    core={'readable':True,'error':None,'last_snapshots_at':published,'last_decisions_at':published}
    def publish():
        (root/'signal-runtime-status.json').write_text(json.dumps(status))
        (root/'IMC_SYSTEM_HEALTH.json').write_text(json.dumps(report))
    fake=SimpleNamespace(load_config=lambda:{'process_timeout_seconds':10,
        'modes':{'PURE_SIGNAL_ONLY':{'conflicting_roles':['LiveSignalGateway']}}},
        process_snapshot=lambda timeout:copy.deepcopy(native),db_probe=lambda *a,**kw:copy.deepcopy(core))
    monkeypatch.setattr(monitor_module,'_SYSTEM_HEALTH',fake)
    monkeypatch.setattr(monitor_module,'RUNTIME_ROOT',root)
    publish()
    return SimpleNamespace(native=native,status=status,report=report,core=core,publish=publish,fake=fake)


def test_runtime_health_uses_native_generation_and_original_data_time(flow,runtime):
    result=monitor_module.runtime_health(flow.store,deadline=time.monotonic()+2)
    assert result['heartbeats']['CORE']['status']=='PASS'
    assert result['heartbeats']['CORE']['observed_at']==runtime.core['last_snapshots_at']
    assert result['heartbeats']['MONITORING']['status']=='PASS'
    assert result['watchdog']['status']=='PASS'
    assert 'MARKET_DATA' not in result['heartbeats']


@pytest.mark.parametrize('fault', ['native-unknown','missing-birth','reused-pid','wrong-worker','wrong-supervisor',
    'stale-supervisor','stale-health','duplicate-role','wrong-gateway','conflicting-role','broadcast',
    'wrong-mode','future-report','report-reused-health','boolean-worker'])
def test_cached_healthy_report_cannot_hide_identity_or_freshness_fault(flow,runtime,fault):
    if fault=='native-unknown':runtime.native['unknown_interpreter_pids']=[123]
    if fault=='missing-birth':runtime.native['processes'][0].pop('CreationDate')
    if fault=='reused-pid':runtime.native['processes'][0]['CreationDate']=flow.clock()
    if fault=='wrong-worker':runtime.status['workers']['CORE']['pid']+=1
    if fault=='wrong-supervisor':runtime.status['pid']+=1
    if fault=='stale-supervisor':runtime.status['updated_at']=(flow.now-timedelta(seconds=16)).isoformat()
    if fault=='stale-health':runtime.report['updated']=(flow.now-timedelta(seconds=16)).isoformat()
    if fault=='duplicate-role':runtime.native['processes'].append(copy.deepcopy(runtime.native['processes'][0]))
    if fault=='wrong-gateway':runtime.native['processes'][2]['ProcessId']+=1
    if fault=='conflicting-role':runtime.native['processes'].append(dict(Role='LiveSignalGateway',ProcessId=999,
        CreationDate=runtime.native['processes'][0]['CreationDate']))
    if fault=='broadcast':runtime.status['broadcast']=True
    if fault=='wrong-mode':runtime.report['configured_mode']='UNKNOWN'
    if fault=='future-report':runtime.report['updated']=(flow.now+timedelta(seconds=1)).isoformat()
    if fault=='report-reused-health':runtime.report['process_snapshot']['processes'][4]['CreationDate']=flow.clock()
    if fault=='boolean-worker':runtime.status['workers']['CORE']['pid']=True
    runtime.publish()
    try:
        result=monitor_module.runtime_health(flow.store,deadline=time.monotonic()+2)
    except (StateError,KeyError):
        return
    assert all(p['status']=='UNKNOWN' for p in result['heartbeats'].values())
    assert result['watchdog']['status']=='UNKNOWN'


def test_core_data_age_cannot_be_renewed_by_fresh_native_or_health_report(flow,runtime):
    runtime.core['last_snapshots_at']=(flow.now-timedelta(seconds=16)).isoformat()
    result=monitor_module.runtime_health(flow.store,deadline=time.monotonic()+2)
    assert result['heartbeats']['CORE']['status']=='UNKNOWN'
    assert result['heartbeats']['MONITORING']['status']=='PASS'


def test_existing_owned_read_lease_limits_runtime_health_and_stop_clears_it(flow,tmp_path):
    flow.store.cfg['reconciliation_poll_seconds']=1000
    monitor=monitor_for(flow,tmp_path/'monitor.json')
    published=flow.clock()
    monitor.health_reader=lambda *a,**k:{'heartbeats':{'CORE':{'status':'PASS','source':'fixture',
        'observed_at':published}},'watchdog':{'status':'PASS','source':'fixture','observed_at':published}}
    monitor.start()
    try:
        wait_monitor_pass(monitor)
        assert monitor.heartbeat()['runtime_health']['watchdog']['status']=='PASS'
        original_deadline=monitor._lease[0]
        flow.now+=timedelta(seconds=5)
        assert monitor.heartbeat()['runtime_health']['heartbeats']['CORE']['observed_at']==published
        assert monitor._lease[0]==original_deadline
        flow.now+=timedelta(seconds=11)
        assert 'runtime_health' not in monitor.heartbeat()
    finally:
        monitor.stop()
    assert 'runtime_health' not in monitor.heartbeat()


@pytest.mark.parametrize('fault', ['none','duplicate-parent','duplicate-worker','worker','role','evidence',
    'boolean-parent','saved-parent-birth','saved-console-birth','reused-child','publication-before-birth'])
def test_native_venv_redirector_must_match_both_held_generations_and_report(flow,runtime,fault):
    parent_birth=(flow.now-timedelta(seconds=12)).isoformat()
    entry=dict(ProcessId=999,CreationDate=parent_birth,Role='Core',
        WorkerPid=runtime.native['processes'][0]['ProcessId'],Evidence='VENV_NATIVE_HANDLE_ARGV_BINDING',
        ConsolePid=998,ConsoleCreated=(flow.now-timedelta(seconds=11)).isoformat())
    runtime.native['interpreter_redirectors']=[entry]
    runtime.report['process_snapshot']['interpreter_redirectors']=copy.deepcopy([entry])
    runtime.status['workers']['CORE']['pid']=999
    if fault=='duplicate-parent':runtime.native['interpreter_redirectors'].append(copy.deepcopy(entry))
    if fault=='duplicate-worker':runtime.native['interpreter_redirectors'].append(dict(entry,ProcessId=997))
    if fault=='worker':entry['WorkerPid']+=1
    if fault=='role':entry['Role']='Watcher'
    if fault=='evidence':entry['Evidence']='PID_ONLY'
    if fault=='boolean-parent':entry['ProcessId']=True
    if fault=='saved-parent-birth':runtime.report['process_snapshot']['interpreter_redirectors'][0]['CreationDate']=(flow.now-timedelta(seconds=13)).isoformat()
    if fault=='saved-console-birth':runtime.report['process_snapshot']['interpreter_redirectors'][0]['ConsoleCreated']=(flow.now-timedelta(seconds=10)).isoformat()
    if fault=='reused-child':runtime.native['processes'][0]['CreationDate']=(flow.now-timedelta(seconds=2)).isoformat()
    if fault=='publication-before-birth':runtime.report['updated']=(flow.now-timedelta(seconds=13)).isoformat()
    runtime.publish()
    try:
        result=monitor_module.runtime_health(flow.store,deadline=time.monotonic()+2)
    except StateError:
        assert fault!='none'
        return
    assert result['heartbeats']['CORE']['status']==('PASS' if fault=='none' else 'UNKNOWN')
    assert result['watchdog']['status']==('PASS' if fault=='none' else 'UNKNOWN')
