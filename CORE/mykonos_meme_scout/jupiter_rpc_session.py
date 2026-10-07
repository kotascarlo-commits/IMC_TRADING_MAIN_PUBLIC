"""One bounded read worker per dispatch; no SEND, keys or persistent broker."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
WORKER=ROOT/'TOOLS/imc_jupiter_prepare_worker.py'
_LOCAL=threading.local()
_RETAINED=[]
_RETAINED_LOCK=threading.Lock()


def current_rpc_session():
    return getattr(_LOCAL,'session',None)


class RPCSession:
    def __init__(self,terminal_fault=None):
        self.process=None;self.thread=None;self.deadline=None;self.calls=0;self.closed=False
        self.jobs=queue.Queue(maxsize=1);self.terminal_fault=terminal_fault

    def bind_deadline(self,deadline):
        if type(deadline) not in (int,float) or not math.isfinite(deadline):raise ValueError('jupiter_rpc_deadline_unknown')
        self.deadline=min(self.deadline,deadline) if self.deadline is not None else deadline
        self.fresh()

    def fresh(self):
        if self.closed or self.deadline is None or time.monotonic()>=self.deadline:
            raise ValueError('jupiter_rpc_session_expired')
        with _RETAINED_LOCK:
            if _RETAINED:raise ValueError('jupiter_rpc_cleanup_unconfirmed')

    def _io(self):
        try:
            for _ in range(24):
                if self.closed:return
                job=self.jobs.get()
                if job is None:return
                body,index,done,result=job
                try:
                    self.process.stdin.write(body);self.process.stdin.flush()
                    line=self.process.stdout.readline(1049601)
                    if not line.endswith(b'\n') or len(line)>1049600:raise ValueError('jupiter_rpc_frame_unknown')
                    from .jupiter_read import unique
                    value=json.loads(line,object_pairs_hook=unique)
                    if not isinstance(value,dict) or set(value)!={'index','response'} or type(value['index']) is not int or value['index']!=index:
                        raise ValueError('jupiter_rpc_sequence_unknown')
                    result.append(value['response'])
                except Exception:
                    result.append(None)
                finally:done.set()
        finally:
            pass

    def __call__(self,method,params,deadline):
        self.bind_deadline(deadline)
        if self.calls>=24:raise ValueError('jupiter_rpc_call_limit')
        message={'operation':'rpc','payload':{'jsonrpc':'2.0','id':1,'method':method,'params':params}}
        body=(json.dumps(message,separators=(',',':'))+'\n').encode()
        if len(body)>4096:raise ValueError('jupiter_rpc_request_unbounded')
        if self.process is None:
            self.process=subprocess.Popen([str(Path(sys._base_executable).resolve(strict=True)),str(WORKER),'--rpc-session'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.thread=threading.Thread(target=self._io,name='imc-jupiter-rpc-io',daemon=True);self.thread.start()
        self.fresh();done=threading.Event();result=[]
        self.jobs.put_nowait((body,self.calls,done,result));self.calls+=1
        if not done.wait(max(0,self.deadline-time.monotonic())):
            self.close();raise ValueError('jupiter_rpc_session_expired')
        self.fresh()
        value=result[0] if result else None
        if not isinstance(value,dict) or set(value)!={'result'}:
            self.close();raise ValueError('jupiter_rpc_read_unknown')
        envelope=value['result']
        if (not isinstance(envelope,dict) or set(envelope)!={'jsonrpc','id','result'}
                or envelope['jsonrpc']!='2.0' or type(envelope['id']) is not int or envelope['id']!=1):
            self.close();raise ValueError('jupiter_rpc_response_unknown')
        return envelope['result']

    def close(self):
        if self.closed:return
        self.closed=True;cleanup=True
        if self.process is not None:
            try:
                if self.process.poll() is None:self.process.kill()
                self.process.wait(timeout=1)
            except Exception:cleanup=False
        try:self.jobs.put_nowait(None)
        except queue.Full:pass
        if self.thread is not None:
            self.thread.join(timeout=1)
            if self.thread.is_alive():cleanup=False
        if not cleanup:
            with _RETAINED_LOCK:_RETAINED.append(self)
            if self.terminal_fault is not None:self.terminal_fault('jupiter_rpc_cleanup_unconfirmed')
            raise ValueError('jupiter_rpc_cleanup_unconfirmed')
        if self.process is not None:
            self.process.stdin.close();self.process.stdout.close()


@contextmanager
def rpc_session(*,terminal_fault=None):
    previous=current_rpc_session()
    if previous is not None:
        yield previous
        return
    session=RPCSession(terminal_fault);_LOCAL.session=session
    try:yield session
    finally:
        try:session.close()
        finally:_LOCAL.session=None
