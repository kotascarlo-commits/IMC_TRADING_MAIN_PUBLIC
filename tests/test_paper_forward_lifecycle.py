"""Bounded runner failure tests: isolated ledgers, fake clock, no processes/APIs."""
import json
import sqlite3
import sys
import subprocess
from pathlib import Path

import pytest
from mykonos_meme_scout import paper_forward as p


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    tick=[1000.]
    monkeypatch.setattr(p.time,'monotonic',lambda:tick[0])
    monkeypatch.setattr(p,'now',lambda:tick[0])
    monkeypatch.setattr(p,'code_hash',lambda:'isolated-runtime-fixture')
    monkeypatch.setattr(p,'assert_closed_owner_gate',lambda:None)
    monkeypatch.setattr(p,'eur_capital_evidence',lambda _: (1_000_000_000,{'initial_eur':1000.,'eur_per_lamport':'0.000001'}))
    source=tmp_path/'source.db'
    with sqlite3.connect(source) as c:c.execute('CREATE TABLE snapshots(id INTEGER PRIMARY KEY)')
    monkeypatch.setattr(p,'SOURCE',source)
    class Event:
        stopped=False
        def is_set(self):return self.stopped
        def set(self):self.stopped=True
        def wait(self,seconds):tick[0]+=seconds;return self.stopped
    monkeypatch.setattr(p.threading,'Event',Event)
    monkeypatch.setattr(p.time,'sleep',lambda seconds:tick.__setitem__(0,tick[0]+seconds))
    path=tmp_path/'paper.db';status=tmp_path/'status.json'
    monkeypatch.setenv('IMC_SHADOW_DATA_ONLY','1')
    monkeypatch.setattr(sys,'argv',[
        'paper_forward','--database',str(path),'--status-path',str(status),
        '--virtual-eur','1000','--quote-wallet',p.SOL,'--duration-seconds','1'])
    return tick,path,status


@pytest.mark.parametrize('error',[sqlite3.OperationalError('fixture'),ValueError('stale_paper_intent'),RuntimeError('state_fault')])
def test_ledger_state_and_integrity_failures_stop_instead_of_retry(runtime,monkeypatch,error):
    monkeypatch.setattr(p.ForwardRunner,'tick',lambda _: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):p.main()
    doc=json.loads(runtime[2].read_text())
    assert doc['stop_reason'].startswith('CRITICAL:')
    assert doc['runtime_state']=='FINISHED_PENDING_NATIVE_EXIT'


def test_expected_provider_failure_is_audited_without_false_fill(runtime,monkeypatch):
    monkeypatch.setattr(p.ForwardRunner,'tick',lambda _: (_ for _ in ()).throw(p.GMGNReadError()))
    p.main()
    doc=json.loads(runtime[2].read_text())
    assert doc['stop_reason']=='DURATION_ELAPSED' and doc['closed']==0
    assert doc['closed_win_rate_pct'] is None and doc['reconciliation']['cash_matches']


def test_collector_no_progress_stops_and_never_publishes_native_stopped(runtime,monkeypatch):
    class Thread:
        def __init__(self,**kwargs):pass
        def start(self):pass
        def join(self,**kwargs):pass
        def is_alive(self):return True
    monkeypatch.setattr(p.threading,'Thread',Thread)
    monkeypatch.setattr(p.ForwardRunner,'tick',lambda _:None)
    sys.argv[-1]='40';sys.argv.append('--collect')
    with pytest.raises(RuntimeError,match='source_progress_stalled'):p.main()
    doc=json.loads(runtime[2].read_text())
    assert doc['runtime_state']=='SOURCE_STOP_UNVERIFIED'
    assert doc['stop_reason']=='SOURCE_THREAD_STOP_UNVERIFIED'


def test_quote_deadline_is_forwarded_and_environment_restored(monkeypatch):
    reader=p.GMGNReader(p.load_config());reader.deadline=p.time.monotonic()+.25
    seen={}
    class Result:
        returncode=0;stdout='{}';stderr=''
    def run(*args,**kwargs):
        seen.update(timeout=kwargs['timeout'],deadline=p.os.environ.get('IMC_GMGN_READ_DEADLINE_UTC'))
        return Result()
    monkeypatch.delenv('IMC_GMGN_READ_DEADLINE_UTC',raising=False)
    monkeypatch.setattr(p.subprocess,'run',run)
    assert reader.read('fixture')=={}
    assert 0<seen['timeout']<=.25 and seen['deadline']
    assert 'IMC_GMGN_READ_DEADLINE_UTC' not in p.os.environ


def test_planned_source_stop_does_not_abort_position_drain(runtime,monkeypatch):
    import inspect
    class Thread:
        def __init__(self,**kwargs):self.target=kwargs['target']
        def start(self):
            source=inspect.getclosurevars(self.target).nonlocals['source']
            source.update(state='SCANNED',cycle_monotonic=runtime[0][0])
        def join(self,**kwargs):pass
        def is_alive(self):return False
    monkeypatch.setattr(p.threading,'Thread',Thread)
    monkeypatch.setattr(p.ForwardRunner,'tick',lambda _:None)
    monkeypatch.setattr(p.PaperBook,'active',lambda _: {'state':'EXITING','last_mark_at':None})
    sys.argv.extend(['--collect','--drain-seconds','40'])
    p.main()
    doc=json.loads(runtime[2].read_text())
    assert doc['stop_reason']=='UNRESOLVED_POSITION_AT_DRAIN_LIMIT'
    assert runtime[0][0]>=41


def test_bootstrap_failure_is_persisted_without_untrusted_provider_messages(runtime,monkeypatch):
    monkeypatch.setattr(p,'eur_capital_evidence',lambda _: (_ for _ in ()).throw(ValueError('fx_document_size')))
    with pytest.raises(ValueError,match='fx_document_size'):p.main()
    doc=json.loads(runtime[2].read_text())
    assert doc['runtime_state']=='BOOTSTRAP_FAILED' and doc['stop_reason']['code']=='fx_document_size'
    assert p.failure_evidence(ValueError('untrusted provider body: secret'))=={'class':'ValueError'}


@pytest.mark.skipif(sys.platform!='win32',reason='Windows process argv classification')
def test_paper_process_identity_never_accepts_aliases_or_unbounded_args():
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(Path(__file__).with_name('paper_process_identity_fixture.ps1'))],capture_output=True,text=True,timeout=10)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'PAPER_PROCESS_IDENTITY_PASS:10' in r.stdout
