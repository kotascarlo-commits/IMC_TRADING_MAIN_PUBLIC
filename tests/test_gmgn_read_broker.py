"""Local IPC and fake Node fixtures. Never starts GMGN or IMC services."""
import os
import json
import subprocess
import sys
import hashlib
import hmac
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows PowerShell 5 script scope')
def test_synchronous_observers_survive_script_scope_and_retained_core(tmp_path):
    entry=Path(__file__).with_name('test_gmgn_observer_script_scope.ps1')
    # Invoke with & from a caller scope, rather than promoting test functions
    # into the process global scope with -File.
    quoted_entry=str(entry).replace("'","''")
    quoted_workspace=str(tmp_path).replace("'","''")
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        f"& '{quoted_entry}' -Workspace '{quoted_workspace}'"],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'GMGN_SYNCHRONOUS_OBSERVER_SCRIPT_SCOPE_PASS' in result.stdout


def fixture_ready(tmp_path, *, version=3):
    broker = {'pid':101,'parent_pid':102,'created_at':'2026-10-03T00:00:00.0000000+00:00',
              'executable':str(tmp_path/'powershell.exe'),'script':str(ROOT/'TOOLS/GMGN-ReadBroker.ps1')}
    parent = dict(broker, pid=102, parent_pid=1, script=str(ROOT/'TOOLS/Start-IMC-Work.ps1'))
    transport = {'quote_mode':'EXIST_QUOTE','sdk_version':'1.6.6','cli_entry':str(tmp_path/'index.js'),
                 **{key:'a'*64 for key in ('cli_sha256','quote_sha256','dependency_sha256','client_sha256','config_sha256','signer_sha256')}}
    return {'schema_version':version,'instance_id':'a'*32,'directory':str(tmp_path),
            'broker':broker,'parent':parent,'transport':transport}


def write_fixture(path, value):
    body = json.dumps(value)
    mac = hmac.new(('a'*64).encode('ascii'), body.encode(), hashlib.sha256).hexdigest()
    path.write_text(json.dumps({'body':body,'mac':mac}), encoding='utf-8')


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows HMAC client')
@pytest.mark.parametrize('mutation', ['old_ready','old_ready_v2','mode','wrapper_hash','sdk_version','instance'])
def test_client_rejects_changed_or_old_identity_before_returning_data(tmp_path, mutation):
    old = mutation in ('old_ready', 'old_ready_v2')
    ready = fixture_ready(tmp_path, version=1 if mutation == 'old_ready' else 2 if old else 3)
    write_fixture(tmp_path/'broker.ready', ready)
    errors = []
    def fake_broker():
        try:
            end = time.monotonic()+4
            while time.monotonic() < end:
                requests = list(tmp_path.glob('*.request.json'))
                if requests: break
                time.sleep(.01)
            else: return
            request = json.loads(json.loads(requests[0].read_text(encoding='utf-8-sig'))['body'])
            if mutation == 'mode': ready['transport']['quote_mode'] = 'SIGNED_QUOTE_PROBE'
            elif mutation == 'wrapper_hash': ready['transport']['quote_sha256'] = 'b'*64
            elif mutation == 'sdk_version': ready['transport']['sdk_version'] = '1.6.7'
            elif mutation == 'instance': ready['instance_id'] = 'b'*32
            write_fixture(tmp_path/'broker.ready', ready)
            write_fixture(tmp_path/(request['id']+'.response.json'),
                {'schema_version':3,'instance_id':request['instance_id'],'id':request['id'],
                 'exit_code':0,'output':'{"wallets":[]}','evidence':None})
        except BaseException as error: errors.append(error)
    thread = threading.Thread(target=fake_broker)
    if not old: thread.start()
    env = dict(os.environ, IMC_GMGN_BROKER_DIR=str(tmp_path), IMC_GMGN_IPC_TOKEN='a'*64)
    result = subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT/'TOOLS/Invoke-GMGN-ReadBroker.ps1'),
                             'portfolio','info','--raw'],env=env,capture_output=True,text=True,timeout=8)
    if not old: thread.join(5)
    assert not errors and result.returncode == 125 and not result.stdout
    if old: assert not list(tmp_path.glob('*.request.json'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker deadline')
def test_client_propagates_remaining_read_deadline_to_isolated_request(tmp_path):
    instance = 'a' * 32
    token = 'a' * 64  # fixture capability, never a broker or provider credential
    binding = {'pid':101,'parent_pid':102,'created_at':'2026-10-03T00:00:00.0000000+00:00',
               'executable':str(tmp_path/'powershell.exe'),'script':str(ROOT/'TOOLS/GMGN-ReadBroker.ps1')}
    parent = dict(binding, pid=102, parent_pid=1, script=str(ROOT/'TOOLS/Start-IMC-Work.ps1'))
    transport = {'quote_mode':'EXIST_QUOTE','sdk_version':'1.6.6','cli_entry':str(tmp_path/'index.js'),
                 **{key:'a'*64 for key in ('cli_sha256','quote_sha256','dependency_sha256','client_sha256','config_sha256','signer_sha256')}}
    body = json.dumps({'schema_version': 3, 'instance_id': instance, 'directory': str(tmp_path),
                       'broker':binding,'parent':parent,'transport':transport})
    mac = hmac.new(token.encode('ascii'), body.encode(), hashlib.sha256).hexdigest()
    (tmp_path / 'broker.ready').write_text(json.dumps({'body': body, 'mac': mac}))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=2)
    env = dict(os.environ)
    env['IMC_GMGN_BROKER_DIR'] = str(tmp_path)
    env['IMC_GMGN_IPC_TOKEN'] = token
    env['IMC_GMGN_READ_DEADLINE_UTC'] = deadline.isoformat(timespec='microseconds')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(ROOT / 'TOOLS/Invoke-GMGN-ReadBroker.ps1'),
                             'portfolio', 'info', '--raw'], env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 124 and 'GMGN_READ_ONLY_BROKER_TIMEOUT' in result.stderr
    requests = list(tmp_path.glob('*.request.json'))
    assert len(requests) == 1
    saved = json.loads(json.loads(requests[0].read_text(encoding='utf-8-sig'))['body'])
    assert abs((datetime.fromisoformat(saved['expires_at']) - deadline).total_seconds()) < .00001
    assert saved['args'] == ['portfolio', 'info', '--raw']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker deadline')
@pytest.mark.parametrize('deadline', ['invalid', '2020-01-01T00:00:00.000000+00:00'])
def test_client_rejects_invalid_or_expired_budget_without_request(tmp_path, deadline):
    env = dict(os.environ)
    env['IMC_GMGN_BROKER_DIR'] = str(tmp_path)
    env['IMC_GMGN_READ_DEADLINE_UTC'] = deadline
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(ROOT / 'TOOLS/Invoke-GMGN-ReadBroker.ps1'),
                             'portfolio', 'info', '--raw'], env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 124 and 'GMGN_READ_BUDGET_INVALID' in result.stderr
    assert not list(tmp_path.glob('*.request.json'))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows broker protocol")
def test_read_broker_protocol(tmp_path):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(Path(__file__).with_name("test_gmgn_read_protocol.ps1")), "-Workspace", str(tmp_path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks passed" in result.stdout
    # Exercise the real bridge consumer on the protocol's projected response.
    sys.path.insert(0, str(ROOT / "RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE"))
    import gmgn_signal_bridge as bridge
    from unittest.mock import patch
    output = (tmp_path / "projected-wallet.json").read_text(encoding="utf-8")
    with patch.object(bridge.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=output)):
        wallet, lamports = bridge.gmgn_sol_wallet()
    assert wallet == "So11111111111111111111111111111111111111112"
    assert lamports == 1_234_567_891
    assert "balances" in json.loads(output)["wallets"][0]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows broker client")
def test_missing_broker_is_generic_and_does_not_fall_back_to_credentials():
    env = dict(os.environ)
    env.pop("IMC_GMGN_BROKER_DIR", None)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "TOOLS/Invoke-GMGN-ReadBroker.ps1"), "portfolio", "info", "--raw"],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 125
    assert not result.stdout
    assert result.stderr.strip() == "GMGN_READ_ONLY_BROKER_UNAVAILABLE"
