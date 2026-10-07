"""Owned pipe cancellation: no network, wallets or live execution."""
import time
import sys
from pathlib import Path
import pytest
from mykonos_meme_scout import jupiter_rpc_session as module
sys.path.insert(0,str(Path(__file__).parents[1]/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))


def worker(tmp_path,monkeypatch,body):
    path=tmp_path/'rpc_fixture.py';path.write_text('import sys,time,json\n'+body,encoding='utf-8')
    monkeypatch.setattr(module,'WORKER',path)


def test_one_worker_bounded_sequence_and_no_deadline_renewal(tmp_path,monkeypatch):
    worker(tmp_path,monkeypatch,"for i in range(24):\n line=sys.stdin.buffer.readline()\n if not line:break\n print(json.dumps({'index':i,'response':{'result':{'jsonrpc':'2.0','id':1,'result':42}}}),flush=True)\n")
    with module.rpc_session() as session:
        first=time.monotonic()+2;session.bind_deadline(first)
        for _ in range(24):assert session('getBlockHeight',[{'commitment':'finalized'}],time.monotonic()+20)==42
        assert session.deadline==first
        with pytest.raises(ValueError,match='call_limit'):session('getBlockHeight',[],time.monotonic()+20)
        process=session.process
    assert process.poll() is not None and not session.thread.is_alive()


@pytest.mark.parametrize('fault',['blocked_stdin','partial_eof','slow_trickle','oversized','wrong_sequence'])
def test_timeout_frame_and_sequence_failures_close_owned_worker(tmp_path,monkeypatch,fault):
    bodies={
        'blocked_stdin':'time.sleep(60)\n',
        'partial_eof':"sys.stdin.buffer.readline();sys.stdout.write('{\"index\":0');sys.stdout.flush()\n",
        'slow_trickle':"sys.stdin.buffer.readline()\nfor i in range(100):\n sys.stdout.write('x');sys.stdout.flush();time.sleep(.02)\n",
        'oversized':"sys.stdin.buffer.readline();sys.stdout.write('x'*1049700+'\\n');sys.stdout.flush()\n",
        'wrong_sequence':"sys.stdin.buffer.readline();print(json.dumps({'index':True,'response':{'result':42}}),flush=True)\n"}
    worker(tmp_path,monkeypatch,bodies[fault])
    started=time.monotonic()
    with module.rpc_session() as session:
        with pytest.raises(ValueError):session('getBlockHeight',[{'commitment':'finalized'}],time.monotonic()+.2)
        process=session.process
    assert time.monotonic()-started<2
    assert process.poll() is not None and not session.thread.is_alive()


def test_cleanup_failure_latches_every_later_session(monkeypatch):
    retained=[];monkeypatch.setattr(module,'_RETAINED',retained);faults=[]
    session=module.RPCSession(faults.append)
    class UnknownProcess:
        def poll(self):return None
        def kill(self):raise OSError()
        def wait(self,timeout):raise TimeoutError()
    session.process=UnknownProcess()
    with pytest.raises(ValueError,match='cleanup_unconfirmed'):session.close()
    assert retained==[session] and faults==['jupiter_rpc_cleanup_unconfirmed']
    with module.rpc_session() as another:
        with pytest.raises(ValueError,match='cleanup_unconfirmed'):another.bind_deadline(time.monotonic()+1)


def test_cached_reader_selects_current_scope_without_retaining_closed_session():
    from execution_jupiter_read import JupiterExecutionReader
    reader=JupiterExecutionReader(None)
    with module.rpc_session() as first:assert reader.rpc is first
    with module.rpc_session() as second:assert reader.rpc is second and second is not first


def test_injected_rpc_and_isolated_domain_never_start_worker(tmp_path):
    from execution_validation_run import IsolatedValidationRun
    with module.rpc_session() as session:
        run=IsolatedValidationRun(tmp_path/'execution.db');run.roundtrip()
        assert session.process is None
    assert run.store.status()['performance']['count']==0
