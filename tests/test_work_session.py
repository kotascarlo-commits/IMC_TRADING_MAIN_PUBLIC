"""Check future session permissions without starting brokers or touching Git."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def run_ps(*args, env=None):
    assert POWERSHELL, "IMC work launcher requires Windows PowerShell"
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=10,
    )


@pytest.mark.parametrize("level,effort", [("routine", "low"), ("standard", "medium"), ("complex", "high")])
def test_native_work_arguments_pin_autonomous_development(level, effort):
    result = run_ps("-Command", f"""
        $ErrorActionPreference = 'Stop'
        . ./TOOLS/IMC_WORK_SESSION.ps1
        $config = & ./TOOLS/Get-IMC-AdvisorConfig.ps1
        @(Get-IMCWorkSessionArguments -Config $config -Level {level}) | ConvertTo-Json -Compress
    """)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "-m", "gpt-6.1-sol", "-c", f'model_reasoning_effort="{effort}"',
        "-c", 'approvals_reviewer="user"', "-s", "danger-full-access",
        "-a", "never", "--search",
    ]


@pytest.mark.parametrize("field,value", [
    ("sandbox_mode", "'workspace-write'"),
    ("sandbox_mode", "'read-only'"),
    ("approval_policy", "'on-request'"),
    ("approval_policy", "'on-failure'"),
    ("approvals_reviewer", "'auto_review'"),
    ("approvals_reviewer", "$null"),
    ("model", "'gpt-6-astra'"),
    ("complex", "'xhigh'"),
])
def test_invalid_work_permissions_or_role_fail_closed(field, value):
    result = run_ps("-Command", f"""
        $ErrorActionPreference = 'Stop'
        . ./TOOLS/IMC_WORK_SESSION.ps1
        $config = & ./TOOLS/Get-IMC-AdvisorConfig.ps1
        $config.work.{field} = {value}
        try {{
            Get-IMCWorkSessionArguments -Config $config -Level complex | Out-Null
            exit 99
        }} catch {{
            Write-Output $_.Exception.Message
            exit 0
        }}
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "IMC development work requires" in result.stdout or "Invalid IMC work" in result.stdout


@pytest.mark.parametrize("level,effort", [("routine", "low"), ("standard", "medium"), ("complex", "high")])
def test_show_config_needs_no_cli_or_broker_and_preserves_index(tmp_path, level, effort):
    index = ROOT / ".git/index"
    before = index.read_bytes()
    lock_present = (ROOT / ".git/index.lock").exists()
    env = dict(os.environ)
    # An early config check must work without resolving or launching external tools.
    env["PATH"] = str(tmp_path)
    env.pop("IMC_ASTRA_BROKER_DIR", None)
    env.pop("IMC_GMGN_BROKER_DIR", None)
    result = run_ps(
        "-File", str(ROOT / "TOOLS/Start-IMC-Work.ps1"),
        "-ShowLaunchConfig", "-Level", level, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "model": "gpt-6.1-sol", "reasoning_effort": effort,
        "sandbox_mode": "danger-full-access", "approval_policy": "never",
        "approvals_reviewer": "user", "git_transport": "native",
        "git_write_verified": False, "applies_to": "new_session",
    }
    assert index.read_bytes() == before
    assert (ROOT / ".git/index.lock").exists() == lock_present
    assert not list(tmp_path.iterdir())


def test_launcher_has_no_git_write_proxy_or_index_probe():
    script = (ROOT / "TOOLS/Start-IMC-Work.ps1").read_text(encoding="utf-8")
    assert "imc-git-write" not in script
    assert "Git-WriteBroker.ps1" not in script
    assert "IMC_GIT_WRITE_BROKER_DIR" not in script
    assert 'Stop-Process' not in script and '-Recurse' not in script
    assert "Remove-Item Env:IMC_ASTRA_BROKER_DIR" in script
    assert '$workBrokerJob.Start(' in script
    assert "GIT_NATIVE_WRITE=NOT_TESTED" in script
    assert "$workArguments = @($workSessionArguments) + @($startupPrompt)" in script
    assert script.index("$env:PATH = $shimDir + ';' + $env:PATH") < script.index("if ($gmgnLoaded -and $realGmgnCli)")
    assert "Remove-Item Env:IMC_GMGN_BROKER_DIR" in script
    result = run_ps("-Command", """
        $ErrorActionPreference = 'Stop'
        foreach ($path in @('./TOOLS/Start-IMC-Work.ps1', './TOOLS/IMC_WORK_SESSION.ps1')) {
            $tokens = $null; $parseErrors = $null
            [System.Management.Automation.Language.Parser]::ParseFile(
                (Resolve-Path $path).Path, [ref]$tokens, [ref]$parseErrors) | Out-Null
            if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
        }
    """)
    assert result.returncode == 0, result.stderr

@pytest.mark.parametrize('harness,marker',[
    ('test_work_progress.ps1','completion guards passed'),
    ('test_work_broker_helpers.ps1','retained-file checks passed'),
])
def test_local_work_helpers(tmp_path,harness,marker):
    result=run_ps('-File',str(Path(__file__).with_name(harness)),'-Workspace',str(tmp_path))
    assert result.returncode == 0,result.stdout+result.stderr
    assert marker in result.stdout

def test_all_work_sources_parse_without_starting_brokers():
    result=run_ps('-Command',"""
      $ErrorActionPreference='Stop'
      foreach($name in @('IMC_WORK_BROKERS','IMC_WORK_PROGRESS','IMC-AdvisorBroker',
                         'Invoke-IMC-AdvisorBroker','Start-IMC-Advisor','Start-IMC-Duo')) {
        $tokens=$null;$errors=$null
        [Management.Automation.Language.Parser]::ParseFile(
          (Join-Path $PWD ('TOOLS/'+$name+'.ps1')),[ref]$tokens,[ref]$errors) | Out-Null
        if ($errors.Count) { throw 'work source syntax invalid' }
      }
    """)
    assert result.returncode == 0,result.stdout+result.stderr

def test_duo_uses_real_child_file_identity_instead_of_inline_work(tmp_path):
    work=tmp_path/'fixture-work.ps1'
    work.write_text('''param([string]$Level)
. ./TOOLS/GMGN-ReadProtocol.ps1
$binding=Get-GmgnProcessBinding $PID $PSCommandPath
@{script=$binding.script;level=$Level}|ConvertTo-Json -Compress
exit 0
''',encoding='utf-8')
    source=(ROOT/'TOOLS/Start-IMC-Duo.ps1').read_text(encoding='utf-8')
    source=source.replace(str(ROOT/'TOOLS/Start-IMC-Work.ps1'),str(work))
    duo=tmp_path/'fixture-duo.ps1';duo.write_text(source,encoding='utf-8')
    result=run_ps('-File',str(duo))
    assert result.returncode == 0,result.stdout+result.stderr
    value=json.loads(result.stdout.splitlines()[-1])
    assert Path(value['script']) == work and value['level']=='complex'


def test_native_transport_preserves_reviewer_and_multiline_prompt(tmp_path):
    node = shutil.which('node.exe')
    assert node, 'IMC native launcher requires Node'
    script = tmp_path / 'arguments.ps1'
    prompt = 'Sol "high"\nAstra read-only; $literal and `literal'
    script.write_text("""
$ErrorActionPreference = 'Stop'
. ./TOOLS/IMC_WORK_SESSION.ps1
$config = & ./TOOLS/Get-IMC-AdvisorConfig.ps1
$prompt = @'
Sol "high"
Astra read-only; $literal and `literal
'@
$arguments = @(Get-IMCWorkSessionArguments -Config $config -Level complex) + @($prompt)
$arguments = @(ConvertTo-IMCNativeArguments -Arguments $arguments)
& $args[0] -e 'process.stdout.write(JSON.stringify(process.argv.slice(1)))' -- @arguments
exit $LASTEXITCODE
""", encoding='utf-8')
    result = run_ps('-File', str(script), node)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = [
        '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort="high"',
        '-c', 'approvals_reviewer="user"', '-s', 'danger-full-access',
        '-a', 'never', '--search', prompt,
    ]
    assert [arg.replace('\r\n', '\n') for arg in json.loads(result.stdout)] == expected


@pytest.mark.parametrize('command', ['portfolio info --raw', 'swap --help'])
def test_read_shim_blocks_real_cli_fallback_without_ready_broker(tmp_path, command):
    marker = tmp_path / 'real-cli-used'
    (tmp_path / 'gmgn-cli.cmd').write_text(
        '@echo off\necho unexpected > "' + str(marker) + '"\nexit /b 0\n',
        encoding='ascii',
    )
    env = dict(os.environ)
    env.pop('IMC_GMGN_BROKER_DIR', None)
    env['PATH'] = ';'.join([
        str(ROOT / 'TOOLS/GMGN_READ_SHIM'), str(tmp_path), str(Path(POWERSHELL).parent),
    ])
    result = run_ps('-Command', f'& gmgn-cli.cmd {command}; exit $LASTEXITCODE', env=env)
    assert result.returncode == 125, result.stdout + result.stderr
    assert not result.stdout
    assert 'GMGN_READ_ONLY_BROKER_UNAVAILABLE' in result.stderr
    assert not marker.exists()
