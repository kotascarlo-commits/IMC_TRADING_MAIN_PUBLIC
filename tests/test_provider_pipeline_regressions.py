"""Provider quota/cache and empty-feed proofs; isolated DBs, no network calls."""
import importlib.util
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mykonos_meme_scout import providers as p
from mykonos_meme_scout.cli import _market_fresh
from mykonos_meme_scout.shadow_session import scan_has_no_candidates
from test_scoring import _base_snapshot


@pytest.fixture
def security(monkeypatch):
    monkeypatch.setattr(p.GoPlusProvider, '_cache', {})
    monkeypatch.setattr(p.GoPlusProvider, '_attempted', {})
    monkeypatch.setattr(p.GoPlusProvider, '_reserve', lambda: 0.)
    calls=[]
    def response(url, *args, **kwargs):
        mint=url.split('=')[-1]
        calls.append(mint)
        return {'code':1, 'result':{mint:{'mintable':{'status':'0'}, 'freezable':{'status':'0'}}}}
    monkeypatch.setattr(p, '_get_json', response)
    return calls


def test_fresh_cache_preserves_stamp_across_new_provider_instances(security):
    item=_base_snapshot()
    a=p.GoPlusProvider().enrich([item])[0]
    b=p.GoPlusProvider().enrich([item])[0]
    assert len(security)==1 and a.security.checked_at==b.security.checked_at
    assert b.security.top10_holder_pct is None and b.security.creator_holder_pct is None


def test_stale_or_future_cache_never_grants_checked_evidence(security,monkeypatch):
    item=_base_snapshot()
    first=p.GoPlusProvider().enrich([item])[0]
    for delta in (-6,1):
        sec=replace(first.security,checked_at=(datetime.now(timezone.utc)+timedelta(seconds=delta)).isoformat())
        p.GoPlusProvider._cache[item.address]=(sec,p.time.monotonic())
        monkeypatch.setattr(p.GoPlusProvider,'_reserve',lambda:10.)
        with pytest.raises(p.ProviderError,match='rate budget'):
            p.GoPlusProvider().enrich([item])
        assert item.address not in p.GoPlusProvider._cache


def test_single_request_rotation_does_not_starve_second_candidate(security):
    items=[_base_snapshot(address='CaseSensitiveA'),_base_snapshot(address='CaseSensitiveB')]
    a=p.GoPlusProvider().enrich(items)
    b=p.GoPlusProvider().enrich(items)
    assert security==['CaseSensitiveA','CaseSensitiveB']
    assert sum(s.security.checked for s in a)==1
    assert sum(s.security.checked for s in b)==2


def test_shared_sqlite_budget_and_retry_after(tmp_path):
    spec=importlib.util.spec_from_file_location('isolated_provider_rate',Path(__file__).parents[1]/'RUNTIME/imc_resilience.py')
    rate=importlib.util.module_from_spec(spec);spec.loader.exec_module(rate)
    rate.DB=tmp_path/'rates.db'
    assert rate.reserve('goplus_public',2.1,wait=False,timeout=.05)==0
    assert rate.reserve('goplus_public',2.1,wait=False,timeout=.05)>1.8
    rate.backoff('goplus_public',31,'HTTP_429',timeout=.05)
    assert rate.reserve('goplus_public',2.1,wait=False,timeout=.05)>30


def test_thirty_second_market_gate_rejects_stale_unknown_future():
    item=_base_snapshot(captured_at=datetime.now(timezone.utc).isoformat())
    assert _market_fresh(item)
    assert not _market_fresh(replace(item,market_data_stale=True))
    assert not _market_fresh(replace(item,captured_at='unknown'))
    assert not _market_fresh(replace(item,captured_at=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat()))
    assert not _market_fresh(replace(item,captured_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()))


@pytest.mark.parametrize('fault',[None,'error','cache','partial','missing','evaluated'])
def test_only_explicit_successful_empty_feed_is_entry_pause(fault):
    scan={'evaluated':0,'scan_success':True,'observation':{'market_state':'NO_CANDIDATES','errors':[],
        'providers':{'gecko':{'state':'FRESH','network_success':True},
                     'dex':{'state':'FRESH','network_success':True},'refresh':{'state':'NOT_REQUESTED'}}}}
    o=scan['observation']
    if fault=='error':o['errors']=['provider_failed']
    elif fault=='cache':o['providers']['gecko']['state']='CACHE'
    elif fault=='partial':o['providers']['dex']['network_success']=False
    elif fault=='missing':o['providers'].pop('dex')
    elif fault=='evaluated':scan['evaluated']=1
    assert scan_has_no_candidates(scan) is (fault is None)


@pytest.mark.parametrize('age,verified,expected',[(0,True,True),(10,True,True),(10.1,True,False),(-1,True,False),(1,False,False),(float('nan'),True,False)])
def test_empty_cache_is_verified_only_within_ten_seconds(age,verified,expected):
    scan={'evaluated':0,'scan_success':True,'observation':{'market_state':'NO_CANDIDATES','errors':[],
        'providers':{'gecko':{'state':'CACHED_EMPTY','network_success':False,'empty_verified':verified,'empty_age_seconds':age},
                     'dex':{'state':'FRESH','network_success':True},'refresh':{'state':'NOT_REQUESTED'}}}}
    assert scan_has_no_candidates(scan) is expected


def test_nonempty_unparseable_gecko_payload_never_becomes_verified_empty(monkeypatch):
    monkeypatch.setattr(p.GeckoTerminalProvider,'_cached',[])
    monkeypatch.setattr(p.GeckoTerminalProvider,'_last_success_monotonic',0.)
    monkeypatch.setattr(p.GeckoTerminalProvider,'_backoff_until_monotonic',0.)
    monkeypatch.setattr(p.GeckoTerminalProvider,'_watch_fallback_active_until_monotonic',0.)
    monkeypatch.setattr(p.GeckoTerminalProvider,'_empty_verified',False)
    monkeypatch.setattr(p,'_get_json',lambda *a,**k:{'data':[{'id':'broken_relationships'}]})
    first=p.GeckoTerminalProvider()
    assert first.discover()==[] and first.last_observation['state']=='DEGRADED'
    second=p.GeckoTerminalProvider()
    assert second.discover()==[] and second.last_observation['state']=='CACHE'
    assert not second.last_observation.get('empty_verified')
