"""Own fixture descendants only; no IMC runtime, wallet, broker or API calls."""
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows interpreter identity')
def test_only_proven_venv_redirectors_are_collapsed():
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(Path(__file__).with_name('test_process_redirectors.ps1'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Eight redirector identity checks passed' in result.stdout


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows contextual learning identity')
def test_learning_worker_requires_complete_owned_native_ancestry():
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(Path(__file__).with_name('test_learning_process_identity.ps1'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Learning identity checks passed: 35' in result.stdout


def prepare(tmp_path):
    (tmp_path / 'tree.py').write_text(
        "import os,subprocess,sys,time\nfrom pathlib import Path\n"
        "Path('parent.pid').write_text(str(os.getpid()))\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "Path('child.pid').write_text(str(child.pid))\ntime.sleep(120)\n", encoding='utf-8')
    (tmp_path / 'args.py').write_text(
        "import sys,json\nfrom pathlib import Path\nPath('arguments.json').write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\n",
        encoding='utf-8')
    (tmp_path / 'orphan.py').write_text(
        "import os,subprocess,sys\nfrom pathlib import Path\n"
        "Path('orphan-parent.pid').write_text(str(os.getpid()))\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "Path('orphan-child.pid').write_text(str(child.pid))\n", encoding='utf-8')
    return ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(Path(__file__).with_name('runtime_job_harness.ps1')),
            '-Workspace', str(tmp_path), '-Python', sys.executable]


def alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87: return False  # PID absent; access/probe errors are failures
        raise ctypes.WinError(error)
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        assert result in (0, 258), f'native exit probe failed: {result}'
        return result == 258
    finally:
        assert kernel.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows job contract')
def test_job_owns_descendants_and_preserves_arguments(tmp_path):
    result = subprocess.run(prepare(tmp_path), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'runtime job checks passed' in result.stdout
    for path in tmp_path.glob('*.pid'):
        assert not alive(int(path.read_text()))
    assert json.loads((tmp_path / 'arguments.json').read_text(encoding='utf-8')) == [
        'with spaces', 'quote"inside', 'tail\\', '', 'Unicode-\u00e4']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native job failures')
@pytest.mark.parametrize('fault', ['assign', 'resume', 'start_wait', 'start_terminate',
                                  'job_query', 'job_terminate', 'job_wait', 'job_timeout',
                                  'active_timeout', 'late_query', 'close', 'release_close',
                                  'member_query','member_moredata','member_open','member_api',
                                  'member_not_owned','member_reject_close'])
def test_job_failures_retain_native_cleanup_ownership(tmp_path, fault):
    prepare(tmp_path)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(Path(__file__).with_name('runtime_job_failure_harness.ps1')),
        '-Workspace', str(tmp_path), '-Python', sys.executable, '-Fault', fault],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'runtime native failure checks passed: {fault}' in result.stdout
    for path in tmp_path.glob('*.pid'):
        assert not alive(int(path.read_text()))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows job crash recovery')
def test_supervisor_crash_closes_job_and_all_descendants(tmp_path):
    process = subprocess.Popen(prepare(tmp_path) + ['-Crash'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / 'ready').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (tmp_path / 'ready').exists()
        pids = [int(p.read_text()) for p in tmp_path.glob('*.pid')]
        assert len(pids) == 2 and all(alive(pid) for pid in pids)
        process.terminate()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.05)
        assert all(not alive(pid) for pid in pids)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
