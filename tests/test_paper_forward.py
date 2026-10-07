"""Synthetic observations in isolated SQLite; never real fills or quality evidence."""
import copy
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path

import pytest
from mykonos_meme_scout import paper_forward as p

MINT='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

@pytest.fixture
def book(tmp_path):
    clock=[1000.]
    value=p.PaperBook(tmp_path/'paper.db',p.load_config(),p.SOL,1_000_000_000,0,
                      clock=lambda:clock[0],policy_hash='synthetic-fixture-only')
    yield value,clock
    value.conn.close()

def snapshot(at=1000,mint=MINT):
    return {'address':mint,'captured_at':datetime.fromtimestamp(at,timezone.utc).isoformat(),'price_usd':1.,
            'security':{'checked_at':datetime.fromtimestamp(at,timezone.utc).isoformat()}}

def quote(book,amount,output,*,buy=False,received=1000):
    return p.validate_quote({'input_token':p.SOL if buy else MINT,'output_token':MINT if buy else p.SOL,
      'input_amount':str(amount),'output_amount':str(output),'min_output_amount':str(output),'slippage':1},
      p.SOL if buy else MINT,MINT if buy else p.SOL,amount,received-.1,received,book.cfg)

def enter(book):
    trade=book.admit(snapshot(),1)
    buy=quote(book,trade['input_lamports'],100000,buy=True)
    sell=quote(book,book.net_output(buy),trade['input_lamports'])
    old=copy.deepcopy(trade)
    book.open(trade,buy,sell)
    return trade,old,buy,sell

def test_duplicate_open_close_and_stale_mark_preserve_cash_and_audit(book):
    b,clock=book
    trade,old,buy,sell=enter(b)
    assert trade['last_mark_at']==1000 and b.meta('evidence_complete')
    cash=b.meta('cash');count=b.conn.execute('SELECT count(*) FROM audit').fetchone()[0]
    with pytest.raises(ValueError,match='stale_paper_intent'):b.open(old,buy,sell)
    assert b.meta('cash')==cash and b.conn.execute('SELECT count(*) FROM audit').fetchone()[0]==count
    stale=copy.deepcopy(trade);b.mark(trade,sell)
    with pytest.raises(ValueError,match='stale_paper_intent'):b.mark(stale,sell)
    b.request_exit(trade,'FIXTURE_EXIT');old_exit=copy.deepcopy(trade)
    b.close(trade,sell);cash=b.meta('cash')
    with pytest.raises(ValueError,match='stale_paper_intent'):b.close(old_exit,sell)
    assert b.meta('cash')==cash
    report=b.report()
    assert report['closed']==1 and report['goals_passed'] is False
    assert report['evidence_class']=='GMGN_QUOTE_PAPER_NOT_CHAIN_FILLS'

def test_second_connection_cannot_replay_open_after_commit(book):
    b,clock=book
    trade=b.admit(snapshot(),1);stale=copy.deepcopy(trade)
    peer=p.PaperBook(b.path,b.cfg,p.SOL,1_000_000_000,0,clock=b.clock,policy_hash='synthetic-fixture-only')
    try:
        buy=quote(b,trade['input_lamports'],100000,buy=True);sell=quote(b,b.net_output(buy),50_000_000)
        b.open(trade,buy,sell);cash=b.meta('cash')
        with pytest.raises(ValueError,match='stale_paper_intent'):peer.open(stale,buy,sell)
        assert peer.meta('cash')==cash
    finally:peer.conn.close()

def test_selection_checkpoint_admission_and_audit_rollback_together(book,monkeypatch):
    b,clock=book
    original=b.event
    def crash(kind,doc):
        if kind=='admitted_before_quote':raise OSError('fixture crash')
        return original(kind,doc)
    monkeypatch.setattr(b,'event',crash)
    with pytest.raises(OSError):b.consider(snapshot(),2,snapshot(999),1,[],{'score':90})
    assert b.meta('checkpoint')==0 and not b.intents()
    assert b.conn.execute("SELECT count(*) FROM audit WHERE kind='selection_decision'").fetchone()[0]==0
    monkeypatch.setattr(b,'event',original)
    trade=b.consider(snapshot(),2,snapshot(999),1,[],{'score':90})
    assert b.meta('checkpoint')==2
    assert trade['selection_evidence']['previous_snapshot_id']==1
    assert trade['selection_evidence']['assessment']['score']==90

def test_atomic_initial_mark_rolls_back_open_on_failure(book,monkeypatch):
    b,clock=book
    trade=b.admit(snapshot(),1);before=copy.deepcopy(trade);cash=b.meta('cash')
    buy=quote(b,trade['input_lamports'],100000,buy=True);sell=quote(b,b.net_output(buy),50_000_000)
    monkeypatch.setattr(b,'_mark',lambda *a,**kw:(_ for _ in ()).throw(OSError('fixture crash')))
    with pytest.raises(OSError):b.open(trade,buy,sell)
    assert b.meta('cash')==cash and b.active()==before and trade==before

@pytest.mark.parametrize('method',['mark','unknown','fail'])
def test_stale_mutations_cannot_overwrite_newer_state(book,method):
    b,clock=book
    trade,old,buy,sell=enter(b);stale=copy.deepcopy(trade)
    b.request_exit(trade,'FIXTURE_EXIT')
    cash=b.meta('cash');failures=b.meta('api_failures')
    with pytest.raises(ValueError,match='stale_paper_intent'):
        getattr(b,method)(stale,sell if method=='mark' else 'fixture_error')
    assert b.active()['state']=='EXITING' and b.meta('cash')==cash and b.meta('api_failures')==failures

def test_entry_failures_are_counted_without_replacing_failed_admission(book):
    b,clock=book
    for i in range(b.cfg['maximum_api_failures']):
        trade=b.admit(snapshot(mint=chr(65+i)*32),i+1)
        b.fail(trade,'entry_quote_unavailable',api_failure=True)
    assert b.meta('halt')=='api_failure_limit' and b.meta('api_failures')==5
    assert len(b.intents())==5 and b.report()['closed']==0 and b.report()['goals_passed'] is False
    assert b.admit(snapshot(mint='Z'*32),6) is None

def test_exit_failure_and_restart_keep_position_and_missing_equity_gap(book):
    b,clock=book
    trade,old,buy,sell=enter(b);b.request_exit(trade,'FIXTURE_EXIT');clock[0]+=31
    b.unknown(trade,'exit_quote_unavailable')
    assert b.meta('evidence_complete') is False and b.active()['state']=='EXITING'
    reopened=p.PaperBook(b.path,b.cfg,p.SOL,1_000_000_000,0,clock=b.clock,policy_hash='synthetic-fixture-only')
    try:
        assert reopened.active()['entry_raw']==trade['entry_raw']
        assert reopened.report()['max_drawdown_pct'] is None
    finally:reopened.conn.close()

@pytest.mark.parametrize('change',['quote_age','signal_age','identity','fake_fill','clock_rollback'])
def test_preopen_rechecks_freshness_identity_and_evidence(book,change):
    b,clock=book
    trade=b.admit(snapshot(),1);buy=quote(b,trade['input_lamports'],100000,buy=True)
    sell=quote(b,b.net_output(buy),50_000_000);cash=b.meta('cash')
    if change=='quote_age':buy.update(requested_at=980,received_at=981)
    if change=='signal_age':clock[0]=1011
    if change=='identity':sell['input_amount']='1'
    if change=='fake_fill':buy['is_fill']=True
    if change=='clock_rollback':clock[0]=999
    with pytest.raises(ValueError):b.open(trade,buy,sell)
    assert b.meta('cash')==cash and b.active()['state']=='ENTERING'

@pytest.mark.parametrize('stamp',[None, False, 1000, [], 'invalid', '1970-01-01T00:16:40',
                                 '1970-01-01T00:16:41+00:00'])
def test_invalid_security_timestamp_rejects_without_crash_or_cash_change(book,stamp):
    b,clock=book
    current=snapshot();current['security']['checked_at']=stamp
    assert any(r.startswith('security_timestamp_') for r in p.brain(current,None,b.cfg,1000))
    trade=b.admit(current,1);cash=b.meta('cash')
    buy=quote(b,trade['input_lamports'],100000,buy=True);sell=quote(b,b.net_output(buy),50_000_000)
    with pytest.raises(ValueError,match='paper_security'):b.open(trade,buy,sell)
    assert b.meta('cash')==cash and b.active()['state']=='ENTERING'

def test_security_expires_between_selection_and_open(book):
    b,clock=book
    current=snapshot();current['security']['checked_at']='1970-01-01T00:16:31+00:00'
    assert not any(r.startswith('security_timestamp_') for r in p.brain(current,None,b.cfg,1000))
    trade=b.admit(current,1);cash=b.meta('cash');clock[0]+=2
    buy=quote(b,trade['input_lamports'],100000,buy=True,received=1002)
    sell=quote(b,b.net_output(buy),50_000_000,received=1002)
    with pytest.raises(ValueError,match='stale_paper_security'):b.open(trade,buy,sell)
    assert b.meta('cash')==cash and b.active()['state']=='ENTERING'

def test_security_expiry_during_quote_validation_blocks_cash_booking(book,monkeypatch):
    b,clock=book
    current=snapshot();current['security']['checked_at']='1970-01-01T00:16:31+00:00'
    trade=b.admit(current,1);cash=b.meta('cash')
    buy=quote(b,trade['input_lamports'],100000,buy=True);sell=quote(b,b.net_output(buy),50_000_000)
    validate=b.validate_observation
    def delayed(*args):validate(*args);clock[0]+=1
    monkeypatch.setattr(b,'validate_observation',delayed)
    with pytest.raises(ValueError,match='stale_paper_security'):b.open(trade,buy,sell)
    assert b.meta('cash')==cash and b.active()['state']=='ENTERING'

@pytest.mark.parametrize('security',[None,False,[],1000])
def test_malformed_security_object_is_a_rejection(book,security):
    b,clock=book
    current=snapshot();current['security']=security
    assert 'invalid_security_schema' in p.brain(current,None,b.cfg,1000)

def test_report_uses_frozen_goals_and_restart_rejects_policy_changes(book):
    b,clock=book
    frozen=b.report()['goals']
    b.cfg['goals']['profit_factor']=0
    assert b.report()['goals']==frozen
    changed=p.load_config();changed['position_fraction']='0.04'
    with pytest.raises(ValueError,match='frozen_cohort_policy_or_wallet_changed'):
        p.PaperBook(b.path,changed,p.SOL,1_000_000_000,0,clock=b.clock,policy_hash='synthetic-fixture-only')
    assert b.conn.execute('SELECT count(*) FROM intents').fetchone()[0]==0

@pytest.mark.parametrize('field,value',[('profit_factor',1.9),('win_rate_pct',69),('max_drawdown_pct',16),
                                      ('top3_net_profit_share_pct',26),('profit_factor',float('nan'))])
def test_config_cannot_weaken_quality_targets(field,value):
    cfg=p.load_config();cfg['goals'][field]=value
    with pytest.raises(ValueError,match='invalid_paper_goals'):p.validate_config(cfg)

def test_security_checked_flag_without_timestamp_is_not_fresh_evidence():
    cfg=p.load_config()
    reasons=p.brain({**snapshot(),'market_data_stale':False,'security':{'checked':True}},None,cfg,1000)
    assert 'security_timestamp_unverified' in reasons

def test_status_access_denied_preserves_existing_file_and_cleans_own_temp(tmp_path,monkeypatch):
    target=tmp_path/'status.json';target.write_text('previous')
    def fail(self,destination):
        error=PermissionError('SECRET');error.winerror=5;raise error
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(PermissionError):p.write_status(target,{'mode':'fixture'})
    assert target.read_text()=='previous' and list(tmp_path.iterdir())==[target]

def test_missing_policy_cannot_reset_existing_cohort(book):
    b,clock=book;b.admit(snapshot(),1)
    b.conn.execute("DELETE FROM meta WHERE key='policy'")
    with pytest.raises(ValueError,match='corrupt_paper_cohort_missing_policy'):
        p.PaperBook(b.path,p.load_config(),p.SOL,1_000_000_000,0,clock=b.clock,policy_hash='synthetic-fixture-only')
    assert len(b.intents())==1 and b.meta('policy') is None

def test_early_code_hash_failure_closes_initialized_connection(tmp_path,monkeypatch):
    original=p.sqlite3.connect;connections=[]
    def connect(*args,**kwargs):
        connection=original(*args,**kwargs);connections.append(connection);return connection
    monkeypatch.setattr(p.sqlite3,'connect',connect)
    monkeypatch.setattr(p,'code_hash',lambda:(_ for _ in ()).throw(OSError('fixture code hash error')))
    with pytest.raises(OSError):p.PaperBook(tmp_path/'early.db',p.load_config(),p.SOL,1_000_000_000,0)
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):connections[0].execute('SELECT 1')

def test_early_pragma_failure_closes_connection(tmp_path,monkeypatch):
    original=p.sqlite3.connect;connections=[]
    class Broken:
        def __init__(self,connection):self.connection=connection;self.closed=False
        def execute(self,query):raise OSError('fixture pragma error')
        def close(self):self.closed=True;self.connection.close()
    def connect(*args,**kwargs):
        connection=Broken(original(*args,**kwargs));connections.append(connection);return connection
    monkeypatch.setattr(p.sqlite3,'connect',connect)
    with pytest.raises(OSError):p.PaperBook(tmp_path/'pragma.db',p.load_config(),p.SOL,1_000_000_000,0)
    assert connections[0].closed

@pytest.mark.parametrize('field',['reserve_lamports','network_fee_lamports_per_side','extra_fee_bps_per_side','maximum_api_failures'])
def test_lamport_costs_and_limits_require_integer_units(field):
    cfg=p.load_config();cfg[field]=cfg[field]+.5
    with pytest.raises(ValueError,match='integer_paper_parameter_required'):p.validate_config(cfg)
