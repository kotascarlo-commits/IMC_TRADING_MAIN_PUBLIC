"""Fake transport/identity only: no GMGN credentials or external requests."""
import subprocess
import json
import os
import sys
import time
import ctypes
from types import SimpleNamespace
from pathlib import Path
import pytest

HERE = Path(__file__).parent


def prepare_quote_fixture(workspace, preloader):
    """Explicit fake entry; environment preloads stay blocked in production."""
    root=HERE.parent
    tools=workspace/'TOOLS'
    tools.mkdir()
    config=workspace/'config'
    config.mkdir()
    for name in ('GMGN-ReadProtocol.ps1','GMGN-SingleSwapTransport.mjs'):
        (tools/name).write_bytes((root/'TOOLS'/name).read_bytes())
    for name in ('imc-gmgn-read-broker.json','imc-execution.json'):
        (config/name).write_bytes((root/'config'/name).read_bytes())
    prefix=('import {fixtureFetch} from '+json.dumps(preloader.as_uri())+';\n'
            "if (globalThis.fetch !== fixtureFetch || typeof fixtureFetch !== 'function') throw Error('FIXTURE_FETCH_REQUIRED');\n")
    entry=tools/'GMGN-SignedQuoteProbe.mjs'
    entry.write_bytes(prefix.encode()+(root/'TOOLS/GMGN-SignedQuoteProbe.mjs').read_bytes())
    return entry

def test_signed_quote_guard_and_redaction():
    result = subprocess.run(['node', str(HERE / 'gmgn_signed_quote_harness.mjs')],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'actual fake fetches=1' in result.stdout

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows process identity')
def test_probe_cannot_hide_normal_supervisor_or_write_runtime_status(tmp_path):
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                             '-File',str(HERE/'test_read_probe_identity.ps1'),'-Workspace',str(tmp_path)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'status isolation checks passed' in result.stdout

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker cooldown')
@pytest.mark.parametrize('body',['html','empty','read_failure','huge_reset'])
@pytest.mark.parametrize('hint',['missing','valid','max','invalid'])
def test_non_json_signed_429_persists_shared_cooldown_without_retry(tmp_path, body, hint):
    run_failed_quote_fixture(tmp_path, body, hint)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker cooldown')
@pytest.mark.parametrize('body',['hanging_body','hanging_cancel','early_cli_deadline','json_array','partial_array','scalar_json'])
def test_incomplete_or_invalid_429_body_persists_unknown_across_restart(tmp_path, body):
    run_failed_quote_fixture(tmp_path, body, 'valid')


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker cooldown')
@pytest.mark.parametrize('retry_after,unknown', [('120', False), ('SECRET', True), ('86401', True)])
def test_http_retry_after_persists_without_reset_header_or_second_fetch(tmp_path, retry_after, unknown):
    run_failed_quote_fixture(tmp_path, 'html', 'missing', retry_after=retry_after,
                             unknown=unknown)
    saved = json.loads((tmp_path/'gmgn-read-cooldown.json').read_text(encoding='utf-8-sig'))
    if not unknown:
        from datetime import datetime
        delay = (datetime.fromisoformat(saved['retry_not_before']) -
                 datetime.fromisoformat(saved['observed_at'])).total_seconds()
        # HTTP delay is never shortened; the existing broker adds one second.
        assert 120 <= delay <= 122


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker cooldown')
@pytest.mark.parametrize('tag', ['RATE_LIMIT_EXCEEDED', 'RATE_LIMIT_BANNED', 'ERROR_RATE_LIMIT_BLOCKED'])
def test_provider_error_enum_crosses_quote_transport_but_not_local_cooldown(tmp_path, tag):
    run_failed_quote_fixture(tmp_path, 'rate_enum', 'valid', api_error=tag)


def run_failed_quote_fixture(tmp_path, body, hint, *, retry_after=None, unknown=False, api_error=None):
    dist = tmp_path/'node_modules/gmgn-cli/dist'
    (dist/'client').mkdir(parents=True)
    (dist.parent/'package.json').write_text('{"type":"module"}',encoding='utf-8')
    (dist/'config.js').write_text('export const getConfig=()=>({});',encoding='utf-8')
    (dist/'client/OpenApiClient.js').write_text('''
export class OpenApiClient {
  async authSignedRequest(method,path,query,body) {
    const url=new URL('https://openapi.gmgn.ai'+path);
    Object.entries({...query,timestamp:String(Math.floor(Date.now()/1000)),client_id:'a1111111-1111-4111-8111-111111111111'})
      .forEach(([k,v])=>url.searchParams.set(k,v));
    const response=await fetch(url.href,{method,body});
    return JSON.parse(await response.text()).data;
  }
}''',encoding='utf-8')
    marker = json.dumps(str(tmp_path/'fetches.txt'))
    start_marker = json.dumps(str(tmp_path/'node-starts.txt'))
    response = "new Response("+json.dumps('<html>SECRET</html>' if body=='html' else '')+",{status:429,headers})"
    if body == 'rate_enum':
        response = "new Response("+json.dumps(json.dumps({'code': 429, 'error': api_error, 'message': 'SECRET', 'upgrade_message': 'SECRET'}))+",{status:429,headers})"
    if body=='read_failure':
        response="({status:429,headers:new Headers(headers),redirected:false,text:async()=>{throw Error('SECRET');}})"
    if body in ('json_array','partial_array','scalar_json'):
        content={'json_array':'[{"reset_at":9999999999999}]','partial_array':'[{"reset_at":', 'scalar_json':'"SECRET"'}[body]
        response="new Response("+json.dumps(content)+",{status:429,headers})"
    if body in ('hanging_body','hanging_cancel','early_cli_deadline'):
        response=("new Response(new ReadableStream({start(c){"+("c.enqueue(new Uint8Array(65537));" if body=='hanging_cancel' else '')+
          "},cancel(){return new Promise(()=>{});}}),{status:429,headers})")
    preloader=tmp_path/'fake-fetch.mjs'
    reset="'9999999999999'" if body=='huge_reset' else 'String(Math.ceil(Date.now()/1000)+31)'
    hint_headers = '' if hint == 'missing' else ",..." + json.dumps({
        'x-ratelimit-limit': '5' if hint == 'valid' else '1000000000' if hint == 'max' else '5 quota_limit=6',
        'x-ratelimit-remaining': '0' if hint != 'invalid' else 'SECRET',
    })
    rate_headers = "{'x-ratelimit-reset':"+reset+hint_headers+"}" if retry_after is None else json.dumps({'retry-after': retry_after})
    preloader.write_text("import {appendFileSync} from 'node:fs';\nappendFileSync("+start_marker+",'1');\n"+
      "appendFileSync("+json.dumps(str(tmp_path/'node-pid.txt'))+",String(process.pid));\n"+
      "globalThis.fetch=async()=>{appendFileSync("+marker+",'1');"+
      "const headers="+rate_headers+";return "+response+";};",encoding='utf-8')
    preloader.write_text(preloader.read_text(encoding='utf-8')+'\nexport const fixtureFetch=globalThis.fetch;\n',encoding='utf-8')
    prepare_quote_fixture(tmp_path,preloader)
    env = dict(os.environ)
    env['NODE_OPTIONS']='--import '+preloader.as_uri()
    command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
      '-File',str(HERE/'test_signed_quote_cooldown.ps1'),'-Workspace',str(tmp_path),'-ModuleDir',str(dist)]
    if body not in ('html','empty','rate_enum') or unknown: command.append('-UnknownCooldown')
    if api_error is not None: command.extend(['-ExpectedApiError', api_error])
    if hint in ('valid','max') and body != 'early_cli_deadline':
        command.extend(['-ExpectedQuotaLimit', '5' if hint == 'valid' else '1000000000',
                        '-ExpectedQuotaRemaining', '0'])
    if body == 'early_cli_deadline':
        command.extend(['-CliTimeoutSeconds','1'])
        process = subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            cooldown = tmp_path/'gmgn-read-cooldown.json'
            until = time.monotonic()+3
            while True:
                try:
                    provisional = json.loads(cooldown.read_text(encoding='utf-8-sig'))
                    break
                except (FileNotFoundError, PermissionError):
                    # Windows may expose creation before the bounded writer
                    # releases its handle. Keep the original observation budget.
                    if process.poll() is not None or time.monotonic() >= until:
                        raise
                    time.sleep(.01)
            assert provisional['unknown'] is True and provisional['http_status'] == 429
            child_pid = int((tmp_path/'node-pid.txt').read_text())
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
            kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p,ctypes.c_ulong,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_ulong)]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x1000, False, child_pid)
            assert handle
            try:
                code = ctypes.c_ulong()
                assert kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259
                name, size = ctypes.create_unicode_buffer(32768), ctypes.c_ulong(32768)
                assert kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size))
                assert name.value.lower().endswith('\\node.exe')
            finally: kernel.CloseHandle(handle)
            # Actual Node is still alive with its body incomplete, not merely
            # the PowerShell parent carrying out later restart checks.
            stdout,stderr = process.communicate(timeout=15)
            result = SimpleNamespace(returncode=process.returncode,stdout=stdout,stderr=stderr)
        finally:
            if process.poll() is None: process.kill();process.wait(2)
    else:
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'shared cooldown passed' in result.stdout
    assert 'SECRET' not in result.stdout+result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows broker child')
@pytest.mark.parametrize('mode',['EXIST_QUOTE','SIGNED_QUOTE_PROBE'])
def test_quote_modes_and_controls_after_config_loading(tmp_path, mode):
    dist = tmp_path/'node_modules/gmgn-cli/dist'
    (dist/'client').mkdir(parents=True)
    (dist.parent/'package.json').write_text('{"type":"module"}',encoding='utf-8')
    (dist/'config.js').write_text("""
process.env.GMGN_DEBUG='SECRET';
process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS='99999';
export function getConfig(required) {
  if (required !== (process.env.IMC_GMGN_QUOTE_MODE === 'SIGNED_QUOTE_PROBE')) throw Error('wrong_key_requirement');
  return {};
}
""",encoding='utf-8')
    (dist/'client/OpenApiClient.js').write_text("""
export class OpenApiClient {
  async authExistRequest(method,path,query) {
    if (process.env.IMC_GMGN_QUOTE_MODE !== 'EXIST_QUOTE') throw Error('wrong_exist_mode');
    return this.go(method,path,query);
  }
  async authSignedRequest(method,path,query) {
    if (process.env.IMC_GMGN_QUOTE_MODE !== 'SIGNED_QUOTE_PROBE') throw Error('wrong_signed_mode');
    return this.go(method,path,query);
  }
  async go(method,path,query) {
    if (process.env.GMGN_DEBUG || process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS !== '0') throw Error('controls_overwritten');
    const url=new URL('https://openapi.gmgn.ai'+path);
    Object.entries({...query,timestamp:String(Math.floor(Date.now()/1000)),client_id:'a1111111-1111-4111-8111-111111111111'})
      .forEach(([k,v])=>url.searchParams.set(k,v));
    return (await fetch(url.href,{method})).json();
  }
}
""",encoding='utf-8')
    preloader = tmp_path/'fake-fetch.mjs'
    preloader.write_text("import {appendFileSync} from 'node:fs';\nappendFileSync("+json.dumps(str(tmp_path/'node-starts.txt'))+",'1');\n"+
      "globalThis.fetch=async()=>{appendFileSync("+json.dumps(str(tmp_path/'fetches.txt'))+",'1');"+
      "return new Response(JSON.stringify({code:429,reset_at:Math.ceil(Date.now()/1000)+31}),{status:200});};",encoding='utf-8')
    preloader.write_text(preloader.read_text(encoding='utf-8')+'\nexport const fixtureFetch=globalThis.fetch;\n',encoding='utf-8')
    prepare_quote_fixture(tmp_path,preloader)
    env = dict(os.environ, NODE_OPTIONS='--import '+preloader.as_uri())
    result = subprocess.run(['powershell.exe','-NoProfile','-File',str(HERE/'test_signed_quote_cooldown.ps1'),
      '-Workspace',str(tmp_path),'-ModuleDir',str(dist),'-QuoteMode',mode,'-ExpectedHttpStatus','200'],env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout+result.stderr
    saved = json.loads((tmp_path/'gmgn-read-cooldown.json').read_text(encoding='utf-8-sig'))
    assert saved['http_status'] == 200 and saved['api_code'] == 429 and saved['unknown'] is False
    assert (tmp_path/'fetches.txt').read_text() == (tmp_path/'node-starts.txt').read_text() == '1'
    assert 'SECRET' not in result.stdout+result.stderr


def test_missing_explicit_fake_import_aborts_before_sdk_or_fetch(tmp_path):
    entry=prepare_quote_fixture(tmp_path,tmp_path/'must-not-exist.mjs')
    result=subprocess.run(['node',str(entry)],capture_output=True,text=True,timeout=5)
    assert result.returncode!=0 and 'ERR_MODULE_NOT_FOUND' in result.stderr
    assert not (tmp_path/'fetches.txt').exists() and not (tmp_path/'node-starts.txt').exists()
