"""Local security observation and historical provenance, with no API requests."""
import copy
import json
import threading
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from mykonos_meme_scout import providers as p
from mykonos_meme_scout import paper_forward as paper
from mykonos_meme_scout.config import Config
from mykonos_meme_scout.engine import PaperEngine
from mykonos_meme_scout.models import SecuritySignals
from mykonos_meme_scout.scoring import assess
from mykonos_meme_scout.store import Store
from test_scoring import _base_snapshot

MINT='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
RAW={'mintable':{'status':'0'},'freezable':{'status':'0'},'checked_at':'2099-01-01T00:00:00Z'}


@pytest.fixture(autouse=True)
def isolated_security_budget(monkeypatch):
    # These fixtures never reserve the production provider quota or reuse another
    # test's cache. Shared-budget behavior has separate SQLite regression tests.
    monkeypatch.setattr(p.GoPlusProvider, '_reserve', lambda: 0.0)
    monkeypatch.setattr(p.GoPlusProvider, '_cache', {})
    monkeypatch.setattr(p.GoPlusProvider, '_attempted', {})

def snapshot():
    return _base_snapshot(address=MINT)

def test_response_timestamp_is_local_request_start_not_payload_or_market(monkeypatch):
    observed=datetime(2026,10,2,18,0,tzinfo=timezone.utc)
    ticks=[100.]
    class Clock:
        @staticmethod
        def now(tz):return observed+timedelta(seconds=ticks[0]-100)
    monkeypatch.setattr(p,'datetime',Clock)
    monkeypatch.setattr(p.time,'monotonic',lambda:ticks[0])
    def response(*args,**kwargs):
        assert kwargs['deadline']==102
        ticks[0]=100.2
        return {'code':1,'result':{MINT:RAW}}
    monkeypatch.setattr(p,'_get_json',response)
    item=snapshot(); result=p.GoPlusProvider().enrich([item])[0]
    assert result.security.checked_at==observed.isoformat()
    assert item.security.checked_at is None
    assert result.captured_at==item.captured_at

@pytest.mark.parametrize('payload',[None,[],{'code':True,'result':{MINT:RAW}},
    {'code':0,'result':{MINT:RAW}}, {'code':1,'result':[]}, {'result':{MINT:RAW}}])
def test_invalid_success_envelope_never_stamps_security(monkeypatch,payload):
    monkeypatch.setattr(p,'_get_json',lambda *a,**k:payload)
    with pytest.raises(p.ProviderError):p.GoPlusProvider().enrich([snapshot()])

@pytest.mark.parametrize('records',[{MINT.lower():RAW},{MINT:{}},{MINT:[]},{}])
def test_wrong_case_missing_or_empty_record_discards_previous_security(monkeypatch,records):
    item=snapshot();item.security.checked_at='2026-10-02T18:00:00Z'
    monkeypatch.setattr(p,'_get_json',lambda *a,**k:{'code':1,'result':records})
    result=p.GoPlusProvider().enrich([item])[0]
    assert result.security.checked is False and result.security.checked_at is None
    assert item.security.checked_at=='2026-10-02T18:00:00Z'

@pytest.mark.parametrize('wall_delta',[2,-2])
def test_worker_rejects_clock_jumps(monkeypatch,wall_delta):
    ticks=[100.];wall=[datetime(2026,10,2,tzinfo=timezone.utc)]
    class Clock:
        @staticmethod
        def now(tz):return wall[0]
    monkeypatch.setattr(p,'datetime',Clock)
    monkeypatch.setattr(p.time,'monotonic',lambda:ticks[0])
    def response(*a,**k):
        ticks[0]+=.1;wall[0]+=timedelta(seconds=wall_delta)
        return {'code':1,'result':{MINT:RAW}}
    monkeypatch.setattr(p,'_get_json',response)
    with pytest.raises(p.ProviderError,match='clock changed'):p.GoPlusProvider._observe('public-fixture',1,102)

def test_queue_deadline_skips_http_and_retry_completion_is_rejected(monkeypatch):
    ticks=[100.];calls=[]
    monkeypatch.setattr(p.time,'monotonic',lambda:ticks[0])
    def response(*a,**k):
        calls.append(1);ticks[0]=103
        return {'code':1,'result':{MINT:RAW}}
    monkeypatch.setattr(p,'_get_json',response)
    with pytest.raises(p.ProviderError,match='queue deadline'):p.GoPlusProvider._observe('fixture',1,100)
    assert calls==[]
    with pytest.raises(p.ProviderError,match='observation late'):p.GoPlusProvider._observe('fixture',1,102)
    assert calls==[1]

def test_read_retry_budget_includes_retry_after_and_skips_next_attempt(monkeypatch):
    ticks=[100.];calls=[];sleeps=[]
    monkeypatch.setattr(p.time,'monotonic',lambda:ticks[0])
    def sleep(delay):sleeps.append(delay);ticks[0]+=delay
    monkeypatch.setattr(p.time,'sleep',sleep)
    def request(req,timeout):
        calls.append(timeout)
        raise urllib.error.HTTPError('https://public-fixture',429,'fixture',{'Retry-After':'30'},None)
    monkeypatch.setattr(p.urllib.request,'urlopen',request)
    with pytest.raises(p.ProviderError,match='deadline'):p._get_json('https://public-fixture',timeout=5,deadline=100.1)
    assert len(calls)==1 and calls[0]==pytest.approx(.1)
    assert sleeps==pytest.approx([.1])

def test_response_body_completion_after_deadline_is_not_returned(monkeypatch):
    ticks=[100.]
    monkeypatch.setattr(p.time,'monotonic',lambda:ticks[0])
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):ticks[0]=103;return b'{"code":1,"result":{}}'
    monkeypatch.setattr(p.urllib.request,'urlopen',lambda *a,**k:Response())
    with pytest.raises(p.ProviderError,match='deadline'):p._get_json('https://public-fixture',deadline=102)

def test_late_worker_cannot_mutate_original_snapshot(monkeypatch):
    release=threading.Event();finished=threading.Event();item=snapshot();before=copy.deepcopy(item)
    def response(*a,**k):
        assert release.wait(2)
        finished.set()
        return {'code':1,'result':{MINT:RAW}}
    monkeypatch.setattr(p,'_get_json',response)
    try:
        with pytest.raises(p.ProviderError):p.GoPlusProvider().enrich([item],hard_deadline=.05)
    finally:release.set()
    assert finished.wait(2)
    assert item==before

def test_sol_security_requests_exactly_one_case_sensitive_mint(monkeypatch):
    calls=[]
    items=[replace(snapshot(),address='A'+str(i)) for i in range(21)]
    def response(url,*a,**k):
        calls.append(url)
        assert ',' not in url
        assert k['max_attempts']==1
        return {'code':1,'result':{s.address:RAW for s in items}}
    monkeypatch.setattr(p,'_get_json',response)
    result=p.GoPlusProvider().enrich(items,hard_deadline=.1)
    assert len(calls)==1 and calls[0].endswith('=A0')
    assert result[0].security.checked_at
    assert all(not s.security.checked for s in result[1:])

def test_repeated_calls_cannot_accumulate_outstanding_http_workers(monkeypatch):
    release=threading.Event();entered=[threading.Event() for _ in range(4)];calls=[]
    # An isolated pool exercises the same process-wide bound without changing
    # any slots belonging to already-running test workers.
    monkeypatch.setattr(p.GoPlusProvider,'_slots',threading.BoundedSemaphore(4))
    def response(*a,**k):
        index=len(calls);calls.append(1);entered[index].set()
        assert release.wait(2)
        return {'code':1,'result':{MINT:RAW}}
    monkeypatch.setattr(p,'_get_json',response)
    try:
        for index in range(4):
            with pytest.raises(p.ProviderError):p.GoPlusProvider().enrich([snapshot()],hard_deadline=.05)
            assert entered[index].is_set()
        with pytest.raises(p.ProviderError,match='outstanding read limit'):
            p.GoPlusProvider().enrich([snapshot()],hard_deadline=.05)
        assert len(calls)==4
    finally:release.set()

def test_store_restart_preserves_observation_and_historical_missing_is_none(tmp_path):
    path=tmp_path/'history.db';store=Store(path);item=snapshot()
    item.security.checked_at='2026-10-02T18:00:00+00:00';store.record_snapshot(item);store.close()
    store=Store(path)
    try:
        assert store.latest_snapshot(MINT).security.checked_at==item.security.checked_at
        assert store.snapshot_at_or_before(MINT,item.captured_at).security.checked_at==item.security.checked_at
        doc=item.as_dict();doc['security'].pop('checked_at')
        store.conn.execute('UPDATE snapshots SET payload_json=?',(json.dumps(doc),));store.conn.commit()
        assert store.latest_snapshot(MINT).security.checked_at is None
        assert store.snapshot_at_or_before(MINT,item.captured_at).security.checked_at is None
    finally:store.close()

def test_position_refresh_carries_original_security_without_restamping(tmp_path,monkeypatch):
    store=Store(tmp_path/'positions.db');item=snapshot()
    item.security.checked_at='2026-10-02T17:00:00+00:00'
    store.record_snapshot(item);store.execute_buy(item,10,1)
    engine=PaperEngine(store,Config());monkeypatch.setattr(engine,'_manage_existing',lambda *a:None)
    refresh=replace(item,captured_at='2026-10-02T18:00:00+00:00',security=SecuritySignals())
    try:
        assert engine.manage_position_snapshot(refresh)['managed']
        assert store.latest_snapshot(MINT).security.checked_at==item.security.checked_at
        assert refresh.security.checked_at is None
    finally:store.close()

def test_scoring_is_unchanged_and_provider_is_in_frozen_policy_hash(monkeypatch):
    item=snapshot();original=assess(item,Config())
    assert assess(replace(item,security=replace(item.security,checked_at='2026-10-02T18:00:00Z')),Config())==original
    before=paper.code_hash();read=paper.Path.read_bytes
    monkeypatch.setattr(paper.Path,'read_bytes',lambda path:read(path)+b'\n# fixture source change' if path.name=='providers.py' else read(path))
    assert paper.code_hash()!=before

@pytest.mark.parametrize('security',[None,False,[],123])
def test_valid_current_with_malformed_previous_security_is_rejected(security):
    item=snapshot();item.security.checked_at=item.captured_at
    current=item.as_dict();previous=copy.deepcopy(current)
    previous['captured_at']='2026-10-01T12:04:59+00:00';previous['security']=security
    reasons=paper.brain(current,previous,paper.load_config(),paper.epoch(item.captured_at))
    assert 'invalid_previous_security_schema' in reasons
