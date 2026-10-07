"""Isolated data/market continuity regressions; no external reads or orders."""
import importlib.util
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess

import pytest
from mykonos_meme_scout import providers
from mykonos_meme_scout.models import SecuritySignals
from mykonos_meme_scout.shadow_observation import bound_targets, record_observation
from mykonos_meme_scout.shadow_session import observe
from mykonos_meme_scout.store import Store
from mykonos_meme_scout.config import Config
from mykonos_meme_scout.cli import watch_open_positions_once
from test_scoring import _base_snapshot

ROOT = Path(__file__).parents[1]
NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize('creators,expected', [([], None), ([{}], None), ([None], None),
    ([{'malicious_address': '0'}], False), ([{'malicious_address':'0'}, {}], None),
    ([{}, {'malicious_address':'1'}], True)])
def test_creator_absence_is_not_safe(creators, expected):
    assert providers.GoPlusProvider._normalize({'creators':creators}).malicious_creator is expected


@pytest.mark.parametrize('value', [True, False, 'NaN', 'inf', '-1', '101', {}, None])
def test_invalid_holder_numbers_remain_unknown(value):
    assert providers.GoPlusProvider._normalize({'top_10_holder_rate':value}).top10_holder_pct is None


def test_partial_holder_list_and_creator_tag_are_not_complete_aggregates():
    raw={'holders':[{'percent':'.01','tag':'creator'}]}
    result=providers.GoPlusProvider._normalize(raw)
    assert result.top10_holder_pct is None and result.creator_holder_pct is None
    raw['holders']*=10
    result=providers.GoPlusProvider._normalize(raw)
    assert result.top10_holder_pct == pytest.approx(10) and result.creator_holder_pct is None
    raw['holders'][5]={}
    assert providers.GoPlusProvider._normalize(raw).top10_holder_pct is None


def pick_db(path, *, pair='PairAddress111', entered=None, old_schema=False):
    c=sqlite3.connect(path)
    c.execute('CREATE TABLE picks(address,variant,accepted,status,entry_at'+('' if old_schema else ',pair_address')+')')
    values=['TokenAddress111','SAFE100',1,'OPEN',(entered or NOW).isoformat()]
    if not old_schema: values.append(pair)
    c.execute('INSERT INTO picks VALUES('+','.join('?' for _ in values)+')',values);c.commit();c.close()


def test_bound_targets_never_guess_legacy_or_expired_pairs(tmp_path):
    old=tmp_path/'old.db';pick_db(old,old_schema=True)
    assert bound_targets(old,observed_at=NOW)==([],['shadow_pair_schema_missing'])
    missing=tmp_path/'missing.db';pick_db(missing,pair=None)
    assert bound_targets(missing,observed_at=NOW)==([],['shadow_pair_binding_missing'])
    expired=tmp_path/'expired.db';pick_db(expired,entered=NOW-timedelta(seconds=601))
    assert bound_targets(expired,observed_at=NOW)==([],[])


def test_bound_target_reads_do_not_change_database(tmp_path):
    path=tmp_path/'picks.db';pick_db(path)
    before=path.read_bytes()
    assert bound_targets(path,observed_at=NOW)==([('TokenAddress111','PairAddress111')],[])
    assert path.read_bytes()==before


@pytest.mark.parametrize('change',[{'pair_address':'DifferentPool'}, {'market_data_stale':True},
    {'captured_at':(NOW-timedelta(seconds=11)).isoformat()}, {'captured_at':(NOW+timedelta(seconds=1)).isoformat()},
    {'price_usd':0}])
def test_shadow_refresh_rejects_pair_stale_and_future_data(tmp_path,change):
    store=Store(tmp_path/'source.db',1000)
    try:
        snapshot=_base_snapshot(**{'captured_at':NOW.isoformat(),**change})
        assert not record_observation(store,snapshot,'PairAddress111',observed_at=NOW)
        assert store.latest_snapshot(snapshot.address) is None
    finally:store.close()


def test_shadow_refresh_preserves_original_security_time_without_entry(tmp_path):
    store=Store(tmp_path/'source.db',1000)
    stamp=(NOW-timedelta(seconds=5)).isoformat()
    previous=_base_snapshot(captured_at=stamp,security=SecuritySignals(checked=True,checked_at=stamp))
    store.record_snapshot(previous)
    current=replace(previous,captured_at=NOW.isoformat(),security=SecuritySignals())
    try:
        assert record_observation(store,current,current.pair_address,observed_at=NOW)
        assert store.latest_snapshot(current.address).security.checked_at==stamp
        assert not store.open_positions()
        assert store.conn.execute('SELECT count(*) FROM decisions').fetchone()[0]==0
    finally:store.close()


def test_core_watcher_records_shadow_picks_without_paper_position(tmp_path):
    picks=tmp_path/'picks.db';pick_db(picks)
    class Feed:
        def fetch_watch_targets(self, targets, **kw):
            assert targets==[('TokenAddress111','PairAddress111')]
            return [_base_snapshot(captured_at=datetime.now(timezone.utc).isoformat())],{'complete':True,'source':'fixture'}
    results,meta=watch_open_positions_once(Config(),tmp_path/'source.db',watch_provider=Feed(),shadow_database=picks)
    assert not results and meta['shadow_observed']==1
    store=Store(tmp_path/'source.db',1000)
    try: assert store.latest_snapshot('TokenAddress111') and not store.open_positions()
    finally:store.close()


def watcher_module():
    import sys
    sys.path.insert(0,str(ROOT/'RUNTIME'))
    spec=importlib.util.spec_from_file_location('bound_watcher_fixture',ROOT/'RUNTIME/watch_breadth_v425_shadow.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_settlement_cannot_use_another_pool_for_same_mint(tmp_path):
    watcher=watcher_module()
    try:
        src=sqlite3.connect(':memory:');src.row_factory=sqlite3.Row
        src.execute('CREATE TABLE snapshots(address,captured_at,price_usd,payload_json)')
        dst=sqlite3.connect(':memory:')
        dst.execute('CREATE TABLE picks(address,variant,entry_at,entry_market,entry_fill,entry_liq,pair_address,status,exit_at,exit_reason,net_pnl)')
        entered=(datetime.now(timezone.utc)-timedelta(seconds=200)).isoformat()
        later=(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()
        dst.execute('INSERT INTO picks VALUES(?,?,?,?,?,?,?, ?,NULL,NULL,NULL)',('mint','SAFE100',entered,1,1,100000,'original','OPEN'))
        payload={'address':'mint','pair_address':'other','market_data_stale':False,'liquidity_usd':100000}
        src.execute('INSERT INTO snapshots VALUES(?,?,?,?)',('mint',later,2,json.dumps(payload)))
        watcher.settle(src,dst)
        assert dst.execute('SELECT status FROM picks').fetchone()[0]=='OPEN'
        payload['pair_address']='original'
        src.execute('UPDATE snapshots SET payload_json=?',(json.dumps(payload),));watcher.settle(src,dst)
        assert dst.execute('SELECT status FROM picks').fetchone()[0]=='CLOSED'
        dst.execute("UPDATE picks SET status='OPEN',pair_address=NULL");watcher.settle(src,dst)
        assert dst.execute('SELECT status FROM picks').fetchone()[0]=='UNVERIFIED_PAIR'
    finally:watcher.V480_SEND.close()


def test_readonly_session_observation_reports_gaps_not_readiness(tmp_path):
    (tmp_path/'CORE/data').mkdir(parents=True);(tmp_path/'RUNTIME').mkdir()
    c=sqlite3.connect(tmp_path/'CORE/data/imc-main.db');c.execute('CREATE TABLE snapshots(id INTEGER PRIMARY KEY,payload_json)')
    d={'captured_at':NOW.isoformat(),'security':{'checked':True,'checked_at':NOW.isoformat()}}
    c.execute('INSERT INTO snapshots VALUES(1,?)',(json.dumps(d),));c.commit();c.close()
    watch={'updated_at':NOW.isoformat(),'feed_complete':True,'shadow_errors':[],'shadow_targets':0,'shadow_observed':0}
    (tmp_path/'RUNTIME/shadow-watch-status.json').write_text(json.dumps(watch))
    (tmp_path/'RUNTIME/watch_breadth_v425_shadow_status.json').write_text(json.dumps({'updated':NOW.isoformat()}))
    result=observe(tmp_path,now=NOW)
    assert result['holder_values_present']==0 and result['qualified_trading_ready'] is False
    assert result['source_age_seconds']==0 and result['latest_id']==1


def test_native_shadow_guard_checks_deadline_clock_freshness_and_limits():
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(ROOT/'tests/shadow_session_fixture.ps1')],capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'SHADOW_GUARD_CHECKS_PASS' in r.stdout


def test_bounded_supervisor_preserves_strict_learning_process_identity():
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(ROOT/'tests/shadow_supervisor_identity_fixture.ps1')],capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'SHADOW_SUPERVISOR_IDENTITY_PASS' in r.stdout


def test_native_hanging_maintenance_read_is_bounded_and_owned():
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(ROOT/'tests/shadow_maintenance_fixture.ps1')],capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'SHADOW_MAINTENANCE_TIMEOUT_CLEANUP_PASS' in r.stdout


def test_late_watch_queue_does_not_issue_http(monkeypatch):
    import time
    monkeypatch.setattr(providers, '_get_json', lambda *a, **k: pytest.fail('late HTTP started'))
    with pytest.raises(providers.ProviderError, match='watch_queue_deadline'):
        providers.DexScreenerProvider._watch_json('https://fixture.invalid', 1, time.monotonic()-1)


def test_discovery_slots_stay_reserved_until_requests_actually_finish(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor, wait
    slots=threading.BoundedSemaphore(4)
    release=threading.Event()
    entered=[]
    def stalled(*a, **kw):
        entered.append(kw['deadline'])
        assert release.wait(2)
        return []
    monkeypatch.setattr(providers.DexScreenerProvider, '_discovery_slots', slots)
    monkeypatch.setattr(providers, '_get_json', stalled)
    pool=ThreadPoolExecutor(max_workers=4)
    futures=[]
    try:
        deadline=time.monotonic()+1
        for _ in range(4):
            futures.append(providers.DexScreenerProvider._submit_discovery(pool,'fixture',.1,deadline))
        for _ in range(20):
            with pytest.raises(providers.ProviderError,match='outstanding discovery limit'):
                providers.DexScreenerProvider._submit_discovery(pool,'fixture',.1,deadline)
        assert not slots.acquire(blocking=False)
    finally:
        release.set();wait(futures);pool.shutdown()
    assert entered==[deadline]*4
    assert slots.acquire(blocking=False)
    slots.release()


def test_hedged_watch_slots_bound_abandoned_requests(monkeypatch):
    import threading
    import time
    slots=threading.BoundedSemaphore(4)
    release=threading.Event()
    calls=[]
    class Stalled:
        def fetch_watch_targets(self, targets, **kw):
            calls.append(kw['deadline'])
            assert release.wait(2)
            return []
    monkeypatch.setattr(providers.ResilientWatchProvider,'_slots',slots)
    feed=providers.ResilientWatchProvider(Stalled(),Stalled())
    try:
        for _ in range(2):
            _,meta=feed.fetch_watch_targets([('mint','pair')],hedge_delay=0,hard_deadline=.03)
            assert not meta['complete']
        for _ in range(10):
            _,meta=feed.fetch_watch_targets([('mint','pair')],hedge_delay=0,hard_deadline=.03)
            assert 'watch_outstanding_capacity' in meta['errors']
        assert len(calls)==4
    finally:
        release.set()
        until=time.monotonic()+2
        while time.monotonic()<until:
            if slots.acquire(blocking=False):
                slots.release();break
            time.sleep(.01)


def test_invalid_pool_cannot_be_counted_as_complete_shadow_feed(tmp_path):
    picks=tmp_path/'picks.db';pick_db(picks)
    class Feed:
        def fetch_watch_targets(self, targets, **kw):
            return [_base_snapshot(pair_address='other',captured_at=datetime.now(timezone.utc).isoformat())],{'complete':True}
    results,meta=watch_open_positions_once(Config(),tmp_path/'source.db',watch_provider=Feed(),shadow_database=picks)
    assert not results and not meta['feed_complete'] and meta['shadow_targets']==1 and meta['shadow_observed']==0


@pytest.mark.parametrize('diagnostic,expected',[(None,1),('1',0),('0',1),('true',1)])
def test_actual_core_loop_suppresses_only_explicit_diagnostic_learner(monkeypatch,tmp_path,diagnostic,expected):
    from mykonos_meme_scout import cli
    if diagnostic is None:monkeypatch.delenv('IMC_SHADOW_DATA_ONLY',raising=False)
    else:monkeypatch.setenv('IMC_SHADOW_DATA_ONLY',diagnostic)
    calls=[]
    class Watcher:
        def __init__(self,**kw):pass
        def start(self):pass
        def join(self,**kw):pass
    class Child:
        def __init__(self,cmd,**kw):calls.append(cmd)
        def poll(self):return 0
    def stop(_):raise KeyboardInterrupt
    monkeypatch.setattr(cli.threading,'Thread',Watcher)
    monkeypatch.setattr(cli.subprocess,'Popen',Child)
    monkeypatch.setattr(cli,'scan_once',lambda *a,**kw:[])
    monkeypatch.setattr(cli.time,'sleep',stop)
    cli.main(['--database',str(tmp_path/'fixture.db'),'run'])
    assert len(calls)==expected
    assert all(cmd[-1]=='ml-auto-once' for cmd in calls)


@pytest.mark.parametrize('failed', [False, True])
def test_diagnostic_scan_heartbeat_only_follows_successful_scan(monkeypatch, tmp_path, failed):
    from mykonos_meme_scout import cli, shadow_session
    root=tmp_path/'CORE';root.mkdir()
    monkeypatch.setattr(cli, 'PROJECT_ROOT', root)
    monkeypatch.setenv('IMC_SHADOW_DATA_ONLY', '1')
    calls=[]
    class Watcher:
        def __init__(self, **kw): pass
        def start(self): pass
        def join(self, **kw): pass
    def scan(*a, **kw):
        if failed: raise providers.ProviderError('fixture')
        return []
    def stop(_): raise KeyboardInterrupt
    monkeypatch.setattr(cli.threading, 'Thread', Watcher)
    monkeypatch.setattr(cli, 'scan_once', scan)
    monkeypatch.setattr(cli.time, 'sleep', stop)
    monkeypatch.setattr(shadow_session, 'native_process_birth', lambda: '133000000000000000')
    monkeypatch.setattr(shadow_session, 'record_scan', lambda *a, **kw: calls.append(a))
    cli.main(['--database', str(root/'data/imc-main.db'), 'run'])
    assert len(calls) == (0 if failed else 1)
    if calls: assert calls[0][1] == 1 and calls[0][3] == 0
