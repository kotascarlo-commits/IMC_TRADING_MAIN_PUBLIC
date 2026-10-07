"""Unsigned isolated transport evidence, never live-market performance evidence."""
import copy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import time

import pytest

from mykonos_meme_scout import reality_accounting as accounting
from mykonos_meme_scout.reality_run import RealityBook, DOMAIN
from mykonos_meme_scout.reality_transport import RealityTransport, validate_evidence
import mykonos_meme_scout.reality_transport as transport_module
from execution_jupiter_shadow import IsolatedBank
from execution_jupiter import JupiterTransactionBuilder
from execution_jupiter_read import JupiterExecutionReader
from execution_state import load_config, timestamp
from mykonos_meme_scout.jupiter_prepare import USDC
from mykonos_meme_scout.jupiter_simulation import associated, TOKEN

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('text,seconds', [('15m',900),('30m',1800),('45m',2700),('1h',3600),
    ('2h',7200),('4h',14400),('8h',28800),('12h',43200),('1m',60),('744h',2678400)])
def test_user_duration(text,seconds):
    assert accounting.duration(text)==seconds


@pytest.mark.parametrize('text', ['',None,'0m','-1h','1.5h','1h ','15','1d','745h','999999h','01h','1H'])
def test_bad_duration(text):
    with pytest.raises(ValueError):accounting.duration(text)


def test_compounding_and_stricter_caps():
    assert accounting.size('1000','1000','.10','10')==100
    assert accounting.size('1050','1050','.10','10')==105
    assert accounting.size('950','950','.10','10')==95
    assert accounting.size('1000','1000','.05','10')==50
    assert accounting.size('1000','70','.10','10')==60
    with pytest.raises(ValueError):accounting.size('1000','10','.10','10')


def closed(tid,gross,net):
    return dict(trade_id=tid, execution_state='CLOSED',reconciled=True,performance_qualified=True,
        gross_eur=str(gross),net_eur=str(net),costs_eur=str(gross-net),network_eur='1',
        execution_eur=str(gross-net-1),slippage_eur='0.5',other_eur='0')


def test_all_losses_and_top3_pf_drawdown_from_curve():
    trades=[closed(str(i),g,n) for i,(g,n) in enumerate([(110,100),(60,50),(30,20),(-10,-20),(0,-10),(10,0)])]
    unknown=dict(closed('unknown',1000,1000),performance_qualified=False)
    result=accounting.metrics(trades+[unknown],[{'equity_eur':v} for v in ('1000','1100','1080','990','1200')])
    assert result['closed']==6 and result['wins']==3 and result['losses']==2
    assert Decimal(result['win_rate'])==Decimal('.5')
    assert Decimal(result['gross_pf'])==21 and Decimal(result['net_pf'])==Decimal(170)/30
    assert result['max_drawdown_eur']=='110' and Decimal(result['max_drawdown_pct'])==10
    assert result['top3_contribution_eur']=='170' and result['top3_profit_share']=='1'
    assert result['net_pf_without_top3']=='0'
    assert result['net_pnl_eur']=='140' and result['gross_pnl_eur']=='200'
    assert result['price_impact_eur'] is None


def test_no_trades_zero_losses_and_unknown_curve_are_not_fake_statistics():
    result=accounting.metrics([], [{'equity_eur':'1000'}])
    assert result['win_rate'] is None and result['gross_pf'] is None
    result=accounting.metrics([closed('1',3,2)], [{'equity_eur':'1000'},{'equity_eur':None},{'equity_eur':'1002'}])
    assert result['net_pf'] is None and result['max_drawdown_eur'] is None
    assert result['equity_curve_complete'] is False


@pytest.fixture
def flow(tmp_path,monkeypatch,request):
    bank=IsolatedBank()
    bank.accounts[bank.vault_a]['lamports']=10**14
    cfg=load_config()
    store=SimpleNamespace(cfg=cfg, clock=timestamp,
        status=lambda:dict(reconciliation_required=False,wallet=bank.wallet,wallet_balance_lamports=str(bank.balance)))
    reader=JupiterExecutionReader(store,fetch_build=bank.build,rpc_reader=bank.rpc,dex='Whirlpool')
    transport=RealityTransport.__new__(RealityTransport)
    transport.cfg=cfg;transport.wallet=bank.wallet
    transport.builder=JupiterTransactionBuilder(store,rpc_reader=bank.rpc)
    monkeypatch.setattr(transport_module,'authority_off',lambda:cfg)
    book=RealityBook(tmp_path/'reality.db')
    overrides=getattr(request,'param',{})
    policy=dict(wallet=bank.wallet,position_fraction='.10',reserve_eur=overrides.get('reserve_eur','0'),
        max_session_trades=3,max_order_fee_lamports=overrides.get('max_order_fee_lamports',cfg['max_order_fee_lamports']))
    run=book.start('30m',policy);book.tick(run['run_id'])
    def leg(side,trade=None):
        at=time.time()
        fx=dict(sol_eur_ask='200',sol_eur_bid='200',usd_per_eur_mid='1',requested_at=at,received_at=at,
                source='ISOLATED_TEST_NOT_CURRENT_FX')
        amount=int(accounting.size(book.get(run['run_id'])['equity_eur'],book.get(run['run_id'])['cash_eur'],'.10',0)/200*10**9) if side=='BUY' else int(trade['remaining_raw'])
        event=dict(side=side,mint=USDC,amount_raw=str(amount),event_id='isolated')
        if side=='BUY':
            store.status=lambda:dict(reconciliation_required=False,wallet=bank.wallet,wallet_balance_lamports=str(amount*10))
            # Production reader sizes from cash; match its quote to the virtual amount.
            bank.balance=amount*10;bank.accounts[bank.wallet]['lamports']=bank.balance
        quote=reader.quote(event);quote['quoted_at']=time.time()
        new,claim=book.claim(run['run_id'],dict(address=USDC,symbol='USDC'),str(len(book.trades(run['run_id']))),
            side=side,trade_id=trade['trade_id'] if trade else None,input_raw=str(amount))
        evidence=transport.execute(claim,quote,fx,Decimal(200)/120 if side=='BUY' else Decimal(200))
        book.fill(claim,evidence,mid_eur=Decimal(200)/120,
                  output_eur=None if side=='BUY' else Decimal(evidence['output_raw'])/10**9*200)
        if side=='BUY':
            position=book.open_trade(run['run_id']);bank.tokens=int(position['remaining_raw'])
            bank.token_account(associated(bank.wallet,USDC,TOKEN),USDC,bank.wallet,bank.tokens)
        else:
            bank.tokens=0;bank.token_account(associated(bank.wallet,USDC,TOKEN),USDC,bank.wallet,0)
        return book.trade(new['trade_id']),claim,evidence
    yield SimpleNamespace(book=book,run=run,leg=leg,bank=bank,transport=transport,reader=reader)
    book.close()


def test_unsigned_buy_position_sell_closed_same_graph_and_exact_costs(flow):
    t,claim,e=flow.leg('BUY')
    assert t['execution_state']=='POSITION_OPEN' and int(t['remaining_raw'])>0
    assert e['signing'] is False and e['chain_fill'] is False and flow.bank.sent==[]
    with pytest.raises(ValueError,match='duplicate'):flow.book.fill(claim,e,mid_eur=Decimal(200)/120)
    t,_,e=flow.leg('SELL',t)
    status=flow.book.status()
    assert t['execution_state']=='CLOSED' and status['open_position'] is None
    assert status['metrics']['closed']==1 and status['metrics']['losses']==1
    assert abs(Decimal(t['gross_eur'])-Decimal(t['costs_eur'])-Decimal(t['net_eur']))<Decimal('.000000001')
    assert Decimal(status['cash_eur'])==1000+Decimal(t['net_eur'])
    assert Decimal(t['slippage_eur'])>0
    assert t['receipts'][0]['output_raw']==t['receipts'][0]['quote']['minimum_output_raw']


def test_second_trade_uses_current_net_equity(flow):
    t,_,_=flow.leg('BUY');t,_,_=flow.leg('SELL',t)
    expected=accounting.size(flow.book.get(flow.run['run_id'])['equity_eur'],flow.book.get(flow.run['run_id'])['cash_eur'],'.1',0)
    t,_,_=flow.leg('BUY')
    assert Decimal(t['stake_eur'])==expected and expected<100


def test_single_run_and_single_position_claim_duplicate_protection(flow):
    with pytest.raises(ValueError,match='active'):flow.book.start('1h',flow.run['policy'])
    t,_,_=flow.leg('BUY')
    with pytest.raises(ValueError,match='entry_not_allowed'):
        flow.book.claim(flow.run['run_id'],dict(address=USDC,symbol='USDC'),'2',input_raw='1')
    with pytest.raises(ValueError,match='quantity'):
        flow.book.claim(flow.run['run_id'],{},'2',side='SELL',trade_id=t['trade_id'],input_raw='1')


@pytest.mark.parametrize('field,value',[('cost_verified',False),('route_verified',False),('signing',True),
    ('broadcast',True),('chain_fill',True),('message_sha256','0'*64),('route_identity','0'*64),
    ('input_raw','1'),('network_lamports','1'),('output_raw','1'),('observed_at',0)])
def test_unknown_or_mismatched_receipt_never_performance(flow,field,value):
    _,claim,e=flow.leg('BUY');broken=dict(e,**{field:value})
    with pytest.raises(ValueError):validate_evidence(claim,broken,time.time())


def test_no_unknown_fee_zero_fallback(flow):
    _,claim,e=flow.leg('BUY')
    for changes in ({'network_eur':'0'},{'other_eur':'123'}, {'units_consumed':1_500_000}):
        with pytest.raises(ValueError):validate_evidence(claim,dict(e,**changes),time.time())


@pytest.mark.parametrize('flow',[{'max_order_fee_lamports':'4999'}],indirect=True)
def test_stricter_frozen_fee_limit_blocks_modeled_fill(flow):
    with pytest.raises(ValueError,match='fee_limit'):flow.leg('BUY')
    assert flow.book.status()['open_position'] is None
    assert flow.book.get(flow.run['run_id'])['cash_eur']=='1000.00'
    assert flow.bank.sent==[]


@pytest.mark.parametrize('flow',[{'reserve_eur':'900'}],indirect=True)
def test_reserve_limited_input_cannot_spend_fees_from_reserve(flow):
    with pytest.raises(ValueError,match='reserve_insufficient'):flow.leg('BUY')
    assert flow.book.get(flow.run['run_id'])['cash_eur']=='1000.00'
    assert flow.book.status()['open_position'] is None
    assert flow.bank.sent==[]


def test_three_session_cap_is_not_reset_on_restart(flow):
    for _ in range(3):
        t,_,_=flow.leg('BUY');flow.leg('SELL',t)
    assert flow.book.status()['metrics']['closed']==3
    peer=RealityBook(flow.book.path)
    try:
        with pytest.raises(ValueError,match='session_trade_limit'):
            peer.claim(flow.run['run_id'],dict(address=USDC),'fourth',input_raw='1')
    finally:peer.close()


def test_atomic_crash_before_closed_commit_preserves_buy_and_exact_rest(flow,monkeypatch):
    t,_,_=flow.leg('BUY');before=flow.book.get(flow.run['run_id'])['cash_eur']
    audit=flow.book.audit
    def fail(run,kind,value):
        if kind=='shadow_reconciled_not_chain':raise OSError('simulated pre-CLOSED commit crash')
        return audit(run,kind,value)
    monkeypatch.setattr(flow.book,'audit',fail)
    with pytest.raises(OSError):flow.leg('SELL',t)
    assert flow.book.get(flow.run['run_id'])['cash_eur']==before
    assert flow.book.open_trade(flow.run['run_id'])['remaining_raw']==t['remaining_raw']
    assert flow.book.status()['metrics']['closed']==0
    monkeypatch.setattr(flow.book,'audit',audit)
    assert flow.book.recover(flow.run['run_id'])['status']=='BLOCKED'
    assert flow.book.open_trade(flow.run['run_id'])['execution_state']=='SELL_UNKNOWN'


@pytest.mark.parametrize('field,value',[('cash_eur','2000'),('policy_hash','0'*64),('start_equity_eur','2000')])
def test_corrupt_ledger_is_not_projected_or_recovered(flow,field,value):
    with flow.book.transaction():
        run=flow.book.get(flow.run['run_id']);run[field]=value;flow.book.save(run)
    with pytest.raises(ValueError):flow.book.status()
    with pytest.raises(ValueError):flow.book.recover(flow.run['run_id'])


def test_closed_receipt_and_cash_audit_detect_tampering(flow):
    t,_,_=flow.leg('BUY');t,_,_=flow.leg('SELL',t)
    assert flow.book.verify(flow.book.get(flow.run['run_id']))
    with flow.book.transaction():
        t=flow.book.trade(t['trade_id']);t['exit_returned_eur']='10000';flow.book.save_trade(t)
    with pytest.raises(ValueError,match='exit_return_corrupt'):flow.book.status()


def test_runtime_error_or_clock_rollback_never_completed(tmp_path):
    tick=[1000.0];b=RealityBook(tmp_path/'clock.db',clock=lambda:tick[0])
    run=b.start('1m',dict(position_fraction='.1',reserve_eur='0',max_session_trades=3,wallet='test'))
    b.tick(run['run_id']);tick[0]-=1
    assert b.tick(run['run_id'])['status']=='BLOCKED'
    assert b.finish(run['run_id'])['status']=='BLOCKED'
    b.close()


def mark_observation(flow,trade,rate):
    fx=dict(sol_eur_ask=str(rate),sol_eur_bid=str(rate),usd_per_eur_mid='1',
        requested_at=time.time(),received_at=time.time(),source='ISOLATED_MARKET_FIXTURE')
    quote=flow.reader.quote(dict(side='SELL',mint=USDC,amount_raw=trade['remaining_raw'],event_id='mark'))
    quote['quoted_at']=time.time()
    evidence=flow.transport.mark(trade,quote,fx)
    value=Decimal(evidence['output_raw'])/10**9*rate-Decimal(evidence['network_eur'])-Decimal(evidence['other_eur'])
    flow.book.mark(flow.run['run_id'],value,mid=Decimal(rate)/120,evidence=evidence)
    return value


def test_net_marks_peak_trailing_and_complete_drawdown(flow):
    from mykonos_meme_scout.execution_rules import price_exit
    from execution_positions import POLICY
    t,_,_=flow.leg('BUY')
    peak=mark_observation(flow,t,240)
    t=flow.book.open_trade(flow.run['run_id'])
    assert Decimal(t['peak_liquidation_eur'])==peak
    assert flow.book.status()['metrics']['equity_curve_complete']
    value=mark_observation(flow,t,200)
    t=flow.book.open_trade(flow.run['run_id'])
    invested=Decimal(t['stake_eur'])+Decimal(t['entry_network_eur'])+Decimal(t['entry_other_eur'])
    assert price_exit(invested,value,Decimal(t['peak_liquidation_eur']),POLICY)=='trailing_stop'
    status=flow.book.status()
    assert Decimal(status['metrics']['max_drawdown_eur'])>0
    assert status['open_position']['last_mark']['net_eur'] is not None


@pytest.mark.parametrize('field',['net_eur','gross_eur','costs_eur','execution_eur','network_eur','slippage_eur','net_pct'])
def test_closed_arithmetic_tampering_fails_before_metrics(flow,field):
    t,_,_=flow.leg('BUY');t,_,_=flow.leg('SELL',t)
    with flow.book.transaction():
        t=flow.book.trade(t['trade_id']);t[field]='12345';flow.book.save_trade(t)
    with pytest.raises(ValueError,match='arithmetic_corrupt'):flow.book.status()


def test_equity_curve_tampering_fails_before_drawdown(flow):
    t,_,_=flow.leg('BUY');mark_observation(flow,t,200)
    flow.book.c.execute("UPDATE equity SET equity_eur='2000' WHERE id=(SELECT MAX(id) FROM equity)")
    with pytest.raises(ValueError,match='equity_curve_corrupt'):flow.book.status()


def test_current_equity_tampering_fails_before_compounded_sizing(flow):
    t,_,_=flow.leg('BUY');flow.leg('SELL',t)
    with flow.book.transaction():
        run=flow.book.get(flow.run['run_id']);run['equity_eur']='2000';flow.book.save(run)
    with pytest.raises(ValueError,match='current_equity_corrupt'):flow.book.status()
    with pytest.raises(ValueError,match='current_equity_corrupt'):flow.book.recover(flow.run['run_id'])
    with pytest.raises(ValueError,match='current_equity_corrupt'):
        flow.book.claim(flow.run['run_id'],dict(address=USDC),'next',input_raw='1')


def test_open_exit_costs_include_execution_loss_without_double_count(flow):
    t,_,_=flow.leg('BUY');mark_observation(flow,t,200)
    p=flow.book.status()['open_position'];mark=p['last_mark']
    qty=Decimal(p['remaining_raw'])/10**p['entry_decimals']
    assert Decimal(mark['estimated_exit_costs_eur'])==qty*Decimal(mark['mid_eur'])-Decimal(mark['value_eur'])
    assert Decimal(mark['estimated_exit_costs_eur'])>0


def test_recovery_verified_position_can_be_shadow_closed(flow):
    t,_,_=flow.leg('BUY')
    assert flow.book.recover(flow.run['run_id'])['status']=='FINISHING'
    flow.leg('SELL',t)
    assert flow.book.finish(flow.run['run_id'])['status']=='COMPLETED'
    assert flow.book.status()['metrics']['closed']==1


@pytest.mark.parametrize('stage',['before_claim','after_claim','after_build','before_fill','after_fill','during_exit','after_sell'])
def test_crash_restart_no_resend_or_phantom(flow,stage):
    if stage not in ('before_claim',):
        if stage in ('after_fill','during_exit','after_sell'):
            trade,_,_=flow.leg('BUY')
            if stage=='after_sell':flow.leg('SELL',trade)
            if stage=='during_exit':flow.book.claim(flow.run['run_id'],{},'exit',side='SELL',trade_id=trade['trade_id'],input_raw=trade['remaining_raw'])
        else:flow.book.claim(flow.run['run_id'],dict(address=USDC,symbol='USDC'),'crash',input_raw='500000000')
    recovered=flow.book.recover(flow.run['run_id'])
    status=flow.book.status()
    if stage in ('after_claim','after_build','before_fill','during_exit'):
        assert recovered['status']=='BLOCKED'
        assert status['equity_eur'] is None
    else:assert recovered['status']=='FINISHING'
    if stage not in ('after_fill','during_exit'):assert status['open_position'] is None
    assert flow.bank.sent==[]


def test_manual_and_automatic_end_and_persistence(tmp_path):
    clock=[1000.0]
    path=tmp_path/'run.db'
    book=RealityBook(path,clock=lambda:clock[0])
    run=book.start('15m',dict(position_fraction='.1',reserve_eur='0',max_session_trades=3,wallet='test'))
    book.tick(run['run_id']);clock[0]+=899
    assert book.status()['remaining_seconds']==1
    clock[0]+=1
    assert book.tick(run['run_id'])['status']=='FINISHING'
    assert book.finish(run['run_id'],cleanup=True)['status']=='COMPLETED'
    book.close();reopened=RealityBook(path,clock=lambda:clock[0])
    assert reopened.status()['duration']=='15m' and reopened.status()['cash_eur']=='1000.00'
    assert reopened.status()['remaining_seconds']==0 and reopened.status()['cleanup_confirmed'] is True
    run=reopened.start('8h',dict(position_fraction='.1',reserve_eur='0',max_session_trades=3,wallet='test'))
    assert reopened.control('stop')['status']=='FINISHING'
    reopened.finish(run['run_id']);reopened.close()


def test_ps_render_header_no_gui_and_invalid_command_duration():
    terminal=str(ROOT/'TOOLS/IMC_Terminal.ps1')
    result=subprocess.run(['powershell.exe','-NoProfile','-File',terminal,'t','status'],capture_output=True,
                          text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0 and 'STATUS:' in result.stdout and 'OWNER OFF' in result.stdout
    for bad in ('0m','-1h','745h','1.5h'):
        result=subprocess.run(['powershell.exe','-NoProfile','-File',terminal,'t','start',bad],capture_output=True,timeout=10)
        assert result.returncode!=0
