"""A promoted advisor client never returns to the environment-only broker."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import re

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows advisor client')
def test_promoted_client_missing_anchor_fails_before_environment_fallback(tmp_path):
    # Execute the exact production client in an OWN tree whose Runtime is empty.
    # This is a filesystem fixture, not a modified or replacement implementation.
    tools_dir = tmp_path / 'TOOLS'
    runtime_dir = tmp_path / 'RUNTIME'
    old_broker = tmp_path / 'old_broker'
    for directory in (tools_dir, runtime_dir, old_broker):
        directory.mkdir()
    client = tools_dir / 'Invoke-IMC-AdvisorBroker.ps1'
    client.write_bytes((ROOT / 'TOOLS/Invoke-IMC-AdvisorBroker.ps1').read_bytes())
    (old_broker / 'broker.ready').write_text('ready')
    env = dict(os.environ, IMC_ASTRA_BROKER_DIR=str(old_broker))
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', str(client), 'OWN_MISSING_ANCHOR_PROBE',
    ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'ASTRA_ADVISOR_ANCHOR_MISSING' in result.stderr
    assert list(old_broker.iterdir()) == [old_broker / 'broker.ready']


def test_bootstrap_bytes_match_fixed_deployment_hash():
    text = (ROOT / 'TOOLS/Invoke-IMC-AdvisorBroker.ps1').read_text(encoding='utf-8-sig')
    expected = re.search(r"\$expectedBootstrapHash='([0-9a-f]{64})'", text).group(1)
    assert hashlib.sha256((ROOT / 'TOOLS/IMC-AdvisorClientBootstrap.ps1').read_bytes()).hexdigest() == expected
