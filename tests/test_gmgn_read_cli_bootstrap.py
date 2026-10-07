"""READ preload contracts; fake fetch only, no SDK config or credentials."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def run_node(source):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    module = (ROOT / 'TOOLS/GMGN-ReadCliBootstrap.mjs').as_uri()
    result = subprocess.run([node, '--input-type=module', '-e',
        f"import {{oneReadFetch,restrictModuleGeneration}} from {json.dumps(module)};\n" + source],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_dotenv_style_controls_are_removed_before_read_and_no_second_fetch():
    result = run_node("""
      process.env.GMGN_DEBUG='1';
      process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS='5000';
      let calls=0, controls=false;
      const read=oneReadFetch(async()=>{
        calls++;controls=!process.env.GMGN_DEBUG && process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS==='0';
        return new Response('{"code":0,"data":{"wallets":[]}}',{status:200});
      });
      await read('https://openapi.gmgn.ai/v1/user/info',{method:'GET'});
      let retryBlocked=false;
      try{await read('https://openapi.gmgn.ai/v1/user/info',{method:'GET'});}catch(e){retryBlocked=e.message==='GMGN_READ_RETRY_BLOCKED';}
      console.log(JSON.stringify({calls,controls,retryBlocked}));
    """)
    assert result == {'calls': 1, 'controls': True, 'retryBlocked': True}


@pytest.mark.parametrize('url,method', [
    ('https://openapi.gmgn.ai/v1/trade/swap', 'POST'),
    ('https://openapi.gmgn.ai/v1/trade/strategy/create', 'POST'),
    ('https://openapi.gmgn.ai/v1/withdrawal', 'POST'),
    ('https://other.invalid/v1/user/info', 'GET'),
    ('https://user:<redacted-email>/v1/user/info', 'GET'),
    ('https://openapi.gmgn.ai/v1/user/info', 'POST'),
])
def test_mutation_and_foreign_routes_are_rejected_before_fetch(url, method):
    result = run_node(f"""
      let calls=0,blocked=false;
      const read=oneReadFetch(async()=>{{calls++;throw Error('should_not_fetch');}});
      try{{await read({json.dumps(url)},{{method:{json.dumps(method)}}});}}
      catch(e){{blocked=e.message==='GMGN_READ_ROUTE_BLOCKED';}}
      console.log(JSON.stringify({{calls,blocked}}));
    """)
    assert result == {'calls': 0, 'blocked': True}


def test_redirect_response_is_rejected_and_not_retried():
    result = run_node("""
      let calls=0,redirectBlocked=false,retryBlocked=false;
      const read=oneReadFetch(async(input,opts)=>{
        calls++;if(opts.redirect!=='error')throw Error('redirect_policy_missing');
        return new Response('',{status:302,headers:{location:'https://other.invalid'}});
      });
      try{await read('https://openapi.gmgn.ai/v1/user/info',{method:'GET'});}catch(e){redirectBlocked=e.message==='GMGN_READ_REDIRECT_BLOCKED';}
      try{await read('https://openapi.gmgn.ai/v1/user/info',{method:'GET'});}catch(e){retryBlocked=e.message==='GMGN_READ_RETRY_BLOCKED';}
      console.log(JSON.stringify({calls,redirectBlocked,retryBlocked}));
    """)
    assert result == {'calls': 1, 'redirectBlocked': True, 'retryBlocked': True}


@pytest.mark.parametrize('kind', ['esm', 'cjs'])
def test_new_shadow_package_cannot_execute_after_generation_is_sealed(tmp_path, kind):
    source = tmp_path / ('entry.mjs' if kind == 'esm' else 'entry.cjs')
    source.write_text("import 'dotenv';" if kind == 'esm' else "require('dotenv');")
    result = run_node(f"""
      import {{mkdirSync,writeFileSync}} from 'node:fs';
      import {{createRequire}} from 'node:module';
      const entry={json.dumps(str(source))};
      const added={json.dumps(str(tmp_path / 'node_modules/dotenv'))};
      restrictModuleGeneration([entry]);
      mkdirSync(added,{{recursive:true}});
      writeFileSync(added+'/package.json',JSON.stringify({{name:'dotenv',main:'index.js'}}));
      writeFileSync(added+'/index.js',"globalThis.injectionRan=true;");
      let blocked=false;
      try{{
        if({json.dumps(kind)}==='esm')await import({json.dumps(source.as_uri())});
        else createRequire(import.meta.url)(entry);
      }}catch(e){{blocked=e.message==='GMGN_READ_MODULE_BLOCKED';}}
      console.log(JSON.stringify({{blocked,executed:globalThis.injectionRan===true}}));
    """)
    assert result == {'blocked': True, 'executed': False}


def test_pinned_modules_and_builtin_require_work(tmp_path):
    source = tmp_path / 'approved.cjs'
    source.write_text("module.exports=require('node:path').basename('approved');")
    result = run_node(f"""
      import {{createRequire}} from 'node:module';
      const entry={json.dumps(str(source))};
      restrictModuleGeneration([entry]);
      console.log(JSON.stringify({{value:createRequire(import.meta.url)(entry)}}));
    """)
    assert result == {'value': 'approved'}
