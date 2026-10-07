"""Isolated function fixtures: never import/execute the production controller."""
import json
import ctypes
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_execution import flow

ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT/'TOOLS/Invoke-IMC-OfflineAudit.ps1'
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows maintenance controller')


def command(tmp_path, case):
    return ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
            str(Path(__file__).with_name('offline_controller_fixture.ps1')), '-Workspace', str(tmp_path),
            '-Python', sys.executable, '-Case', case]


@pytest.mark.parametrize('gate,remaining,event,version', [('OFF','0','READY',2), ('ON','0','READY',2),
    ('OFF','1','READY',2), ('OFF','0','UNKNOWN',2), ('OFF','0','READY',True)])
def test_optimized_environment_cannot_disable_safety(tmp_path, flow, gate, remaining, event, version):
    flow.buy()
    with sqlite3.connect(flow.store.path) as source, sqlite3.connect(tmp_path/'ledger.db') as connection:
        source.backup(connection)
        remove_trade_guards(connection)
        doc=json.loads(connection.execute('SELECT doc FROM trades').fetchone()[0])
        assert 'state' not in doc and doc['execution_state']=='READY'
        doc['remaining_raw']=remaining
        connection.execute('UPDATE trades SET doc=?',(json.dumps(doc),))
        doc=json.loads(connection.execute('SELECT doc FROM events').fetchone()[0]);doc['state']=event
        connection.execute('UPDATE events SET doc=?',(json.dumps(doc),))
        connection.execute('UPDATE meta SET value=? WHERE key=?',(json.dumps(version),'schema_version'))
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':gate}))
    result = subprocess.run(command(tmp_path,'safety'),capture_output=True,text=True,timeout=10)
    valid = (gate,remaining,event,version)==('OFF','0','READY',2) and type(version) is int
    assert result.returncode == (0 if valid else 2), result.stdout+result.stderr
    assert ('PASS' if valid else 'REJECT') in result.stdout


def remove_trade_guards(connection):
    import execution_machine
    for name in execution_machine.WRITE_GUARDS:connection.execute('DROP TRIGGER '+name)


@pytest.mark.parametrize('state',['CANDIDATE','RISK_CHECK','READY','REJECTED','REJECTED_EVENT'])
def test_real_schema2_passive_storage_is_accepted(tmp_path,flow,state):
    if state=='READY':flow.buy()
    elif state=='REJECTED_EVENT':
        event=flow.buy()
        assert flow.store.feedback(flow.data(event,'REJECTED'))
    else:
        tid=flow.candidate()
        if state!='CANDIDATE':
            with flow.store.transaction() as c:
                trade=flow.store.get(c,'trades',tid)
                flow.store.machine_transition(c,trade,state,'isolated-offline-test')
    with sqlite3.connect(flow.store.path) as source, sqlite3.connect(tmp_path/'ledger.db') as target:source.backup(target)
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    result=subprocess.run(command(tmp_path,'safety'),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('corruption',['missing','wrong_type','legacy','ambivalent','duplicate','unknown',
    'active_zero','nonzero_buy','sent','order','signature','filled','orphan','wrong_mint','wrong_wallet',
    'missing_active','wrong_side','exit','unknown_reason','rejected_ready','extra_ready'])
def test_real_schema2_corruption_is_rejected_before_stop(tmp_path,flow,corruption):
    flow.buy()
    with sqlite3.connect(flow.store.path) as source, sqlite3.connect(tmp_path/'ledger.db') as c:
        source.backup(c);remove_trade_guards(c)
        text=c.execute('SELECT doc FROM trades').fetchone()[0];trade=json.loads(text)
        event=json.loads(c.execute('SELECT doc FROM events').fetchone()[0])
        if corruption=='missing':del trade['execution_state']
        elif corruption=='wrong_type':trade['execution_state']=True
        elif corruption=='legacy':trade['state']='APPROVED'
        elif corruption=='ambivalent':trade.update(state='CANDIDATE',execution_state='BUY_REQUESTED')
        elif corruption=='unknown':trade['execution_state']='APPROVED'
        elif corruption=='active_zero':trade['execution_state']='BUY_REQUESTED'
        elif corruption=='nonzero_buy':trade.update(buy_raw='1',sold_raw='1')
        elif corruption=='sent':event['sent_at']='2026-10-03T00:00:00+00:00'
        elif corruption=='order':event['order_id']='isolated-order'
        elif corruption=='signature':event['signature']='isolated-signature'
        elif corruption=='filled':event['filled_output_raw']='1'
        elif corruption=='orphan':event['trade_id']='missing-trade'
        elif corruption=='wrong_mint':event['mint']='different-mint'
        elif corruption=='wrong_wallet':event['wallet']='different-wallet'
        elif corruption=='missing_active':trade['execution_event_id']='missing-event'
        elif corruption=='wrong_side':event['side']='SELL'
        elif corruption=='exit':trade['exit_requested']=True
        elif corruption=='unknown_reason':trade['unknown_reason']='RECOVERY'
        elif corruption=='rejected_ready':trade['execution_state']='REJECTED'
        elif corruption=='extra_ready':
            extra=dict(event,event_id='extra-isolated-event')
            c.execute('INSERT INTO events(id,doc) VALUES(?,?)',(extra['event_id'],json.dumps(extra)))
        stored=json.dumps(trade)
        if corruption=='duplicate':stored=stored[:-1]+',"execution_state":"CANDIDATE"}'
        c.execute('UPDATE trades SET doc=?',(stored,))
        c.execute('UPDATE events SET doc=? WHERE id=?',(json.dumps(event),event['event_id']))
        if corruption=='extra_ready':
            rows=list(c.execute('SELECT id,doc FROM events'))
            assert len(rows)==2 and all(key==json.loads(text)['event_id'] for key,text in rows)
            assert trade['execution_event_id']==event['event_id']!='extra-isolated-event'
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    result=subprocess.run(command(tmp_path,'safety'),capture_output=True,text=True,timeout=10)
    assert result.returncode==2,result.stdout+result.stderr


def test_diagnostic_codes_never_include_arbitrary_exception_text(tmp_path):
    result=subprocess.run(command(tmp_path,'diagnostic'),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=='isolated diagnostic PASS'


def test_retained_logging_and_output_failures_keep_real_guard_alive(tmp_path):
    protected = tmp_path/'guarded-file'
    protected.write_bytes(b'fixture')
    child = subprocess.Popen(command(tmp_path,'retain'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic()+5
        while not (tmp_path/'ready').exists() and time.monotonic()<deadline:
            assert child.poll() is None
            time.sleep(.02)
        assert (tmp_path/'ready').exists()
        time.sleep(.15)
        assert child.poll() is None
        with pytest.raises(PermissionError):
            with protected.open('r+b'): pass
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=3)
    with protected.open('r+b'): pass


def mutex_api():
    api = ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenMutexW.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_wchar_p];api.OpenMutexW.restype=ctypes.c_void_p
    api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    api.ReleaseMutex.argtypes=[ctypes.c_void_p];api.CloseHandle.argtypes=[ctypes.c_void_p]
    return api


def wait_marker(path, child):
    deadline=time.monotonic()+5
    while not path.exists() and time.monotonic()<deadline:
        assert child.poll() is None
        time.sleep(.01)
    assert path.exists()


def test_post_release_logging_failures_release_while_controller_is_still_alive(tmp_path):
    protected=tmp_path/'guarded-file';protected.write_bytes(b'fixture')
    child=subprocess.Popen(command(tmp_path,'released'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    api=mutex_api();handle=None
    try:
        wait_marker(tmp_path/'mutex-ready',child)
        handle=api.OpenMutexW(0x100001,False,'Local\\IMC_RELEASED_FIXTURE_'+str(child.pid))
        assert handle, ctypes.get_last_error()
        assert api.WaitForSingleObject(handle,0)==258
        (tmp_path/'parent-opened').write_text('opened')
        wait_marker(tmp_path/'finished',child)
        assert child.poll() is None  # release proof before OS exit can close resources
        with protected.open('r+b'): pass
        assert api.WaitForSingleObject(handle,0)==0
        assert api.ReleaseMutex(handle)
        (tmp_path/'parent-checked').write_text('checked')
        output,error=child.communicate(timeout=3)
        assert child.returncode==0,output+error
    finally:
        if child.poll() is None:child.kill()
        child.wait(timeout=3)
        if handle:assert api.CloseHandle(handle)


def test_partial_release_keeps_mutex_and_clears_false_cleanup_flags(tmp_path):
    protected=tmp_path/'guarded-file';protected.write_bytes(b'fixture')
    child=subprocess.Popen(command(tmp_path,'partial'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    api=mutex_api();handle=None
    try:
        wait_marker(tmp_path/'partial-saved',child)  # Save-Record completed before fixture output callback
        report=json.loads((tmp_path/'partial-report.json').read_text(encoding='utf-8-sig'))
        assert report['phase']=='RETAINED' and report['restart_allowed'] is False and report['cleanup_confirmed'] is False
        assert child.poll() is None
        with protected.open('r+b'): pass
        handle=api.OpenMutexW(0x100001,False,'Local\\IMC_PARTIAL_FIXTURE_'+str(child.pid))
        assert handle,ctypes.get_last_error()
        assert api.WaitForSingleObject(handle,0)==258
    finally:
        if child.poll() is None:child.kill()
        child.wait(timeout=3)
        if handle:assert api.CloseHandle(handle)
