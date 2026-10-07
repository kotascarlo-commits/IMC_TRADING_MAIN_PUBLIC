"""Accounting fixtures are synthetic tests, never forward performance evidence."""
import json

import pytest

from mykonos_meme_scout import paper_forward as p
from test_paper_forward import book, snapshot, quote


def test_closed_winrate_excludes_failed_intents_and_reconciles_all_losses(book):
    b,clock=book
    with b.transaction():
        b.set_meta('capital_evidence',{'initial_eur':1000.,'eur_per_lamport':'0.000001'})
    for i,ratio in enumerate((1.20,.85)):
        mint=('A' if i==0 else 'B')*32
        trade=b.admit(snapshot(at=clock[0],mint=mint),i+1)
        buy=p.validate_quote({'input_token':p.SOL,'output_token':mint,'input_amount':str(trade['input_lamports']),
            'output_amount':'100000','min_output_amount':'99000','slippage':1},p.SOL,mint,
            trade['input_lamports'],clock[0]-.1,clock[0],b.cfg)
        raw=b.net_output(buy)
        sell=p.validate_quote({'input_token':mint,'output_token':p.SOL,'input_amount':str(raw),
            'output_amount':str(int(trade['input_lamports']*ratio)),
            'min_output_amount':str(int(trade['input_lamports']*ratio*.99)),'slippage':1},mint,p.SOL,
            raw,clock[0]-.1,clock[0],b.cfg)
        b.open(trade,buy,sell);b.request_exit(trade,'FIXTURE');b.close(trade,sell)
    failed=b.admit(snapshot(at=clock[0],mint='C'*32),3);b.fail(failed,'fixture_provider_error')
    r=b.report()
    assert r['admitted']==3 and r['closed']==2
    assert r['winners']==1 and r['losers']==1 and r['closed_win_rate_pct']==50
    assert r['win_rate_pct']==pytest.approx(100/3)  # Legacy cohort-admission metric remains explicit.
    assert r['reconciliation']['cash_matches'] and r['reconciliation']['closed_matches_exit_events']
    assert r['end_capital_eur']==pytest.approx(1000+r['closed_net_pnl_eur'])
    assert r['profit_factor_without_top3']==0
    assert r['network_cost_eur']>0 and r['fee_haircut_estimate_eur']>0 and r['slippage_reserve_estimate_eur']>0
    assert len(r['top20'])==2 and r['flop20'][0]['net_pnl_eur']<0
    assert r['performance_reliable'] is False


def test_no_trades_keeps_statistics_undefined_and_cash_reconciled(book):
    b,_=book
    with b.transaction():b.set_meta('capital_evidence',{'initial_eur':1000.,'eur_per_lamport':'0.000001'})
    r=b.report()
    assert r['closed_win_rate_pct'] is None and r['profit_factor'] is None
    assert r['average_trade_eur'] is None and r['closed_net_pnl_eur'] is None
    assert r['end_capital_eur']==1000 and r['reconciliation']['cash_matches']


def test_tampered_cash_does_not_pass_reconciliation(book):
    b,_=book
    with b.transaction():b.set_meta('cash',b.meta('cash')+1)
    assert b.report()['reconciliation']['cash_matches'] is False


def test_missing_security_is_unknown_not_passed(book):
    b,clock=book;evidence={}
    current=snapshot();current.update(market_data_stale=False)
    reasons=p.brain(current,None,b.cfg,clock[0],evidence=evidence)
    assert reasons and evidence['security_complete'] is False
    assert evidence['holder_complete'] is False
    assert evidence['security_checks']['top10_holder_pct']['status']=='UNKNOWN'
    assert evidence['security_checks']['mint_authority_disabled']['status']=='UNKNOWN'
