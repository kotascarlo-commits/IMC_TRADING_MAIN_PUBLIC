"""Actual one-time operator control flow with isolated dependency doubles."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT=Path(__file__).parents[1]


def fixture(path):
    folder=path/'.local-tools/offline-audit-4af28cf46a4444cb90881ddb3a4f7981'
    folder.mkdir(parents=True)
    safety={'gate':'OFF','schema_version':2,'positions':0,'uncertain':0,'receipts':0}
    (folder/'controller.json').write_text(json.dumps({'pid':18720,'nonce':'4af28cf46a4444cb90881ddb3a4f7981',
        'phase':'RETAINED','stage':'POST_STOP_SAFETY','stopped':True,'restart_allowed':False,'cleanup_confirmed':False,
        'absence_before':{'runtime_roles':0,'unknown_interpreters':0},'safety_before':safety,'safety_immediately_before_stop':safety}))
    return path


@pytest.mark.skipif(os.name!='nt',reason='Windows operator')
@pytest.mark.parametrize('case',['success','identity','legacy','close','crash'])
def test_actual_retained_handover_fails_closed(tmp_path,case):
    path=fixture(tmp_path)
    child=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'tests/retained_repair_fixture.ps1'),'-Workspace',str(path),'-Python',sys.executable,'-Case',case],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        if case=='success':
            stdout,stderr=child.communicate(timeout=8)
            assert child.returncode==0,stdout+stderr
            assert json.loads((path/'state.json').read_text())['state']=='IDLE'
            result=json.loads(next((path/'.local-tools').glob('retained-repair-*/repair.json')).read_text())
            assert result['phase']=='FINAL' and result['cleanup_confirmed'] is True and result['restart_allowed'] is True
        else:
            ready=path/('crash-ready' if case=='crash' else 'retained-ready')
            deadline=time.monotonic()+8
            while not ready.exists() and time.monotonic()<deadline:
                assert child.poll() is None,child.communicate()
                time.sleep(.01)
            assert ready.exists()
            if case=='identity':
                assert not (path/'state.json').exists()
            else:
                assert json.loads((path/'state.json').read_text())['state']=='ACTIVE'
            if case!='crash':
                result=json.loads(next((path/'.local-tools').glob('retained-repair-*/repair.json')).read_text())
                assert result['phase']=='RETAINED' and result['restart_allowed'] is False
        calls=(path/'calls').read_text().splitlines()
        if case=='identity':
            assert 'marker.active' not in calls and 'old.kill' not in calls
        else:
            assert calls.index('marker.active')<calls.index('recovery.verified')<calls.index('old.kill')<calls.index('old.exit')<calls.index('legacy.takeover')
        if case=='success':
            assert calls.count('legacy.takeover')==2
            assert calls.index('old.dispose')<calls.index('marker.idle')<calls.index('fence.exit')
        else:
            assert 'marker.idle' not in calls and 'fence.exit' not in calls
    finally:
        if child.poll() is None:child.kill()
        child.communicate(timeout=3)
    if case in ('legacy','close','crash'):
        # The durable block survives the operator's actual process death.
        assert json.loads((path/'state.json').read_text())['state']=='ACTIVE'
