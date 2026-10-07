"""Full runtime dispatch with isolated market/RPC transport; not forward proof."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
import time
from types import SimpleNamespace
import pytest

from RUNTIME import reality_runtime as runtime
from mykonos_meme_scout.reality_run import RealityBook
from mykonos_meme_scout.models import TokenSnapshot, SecuritySignals
from mykonos_meme_scout.reality_transport import RealityTransport
import mykonos_meme_scout.reality_transport as module
from execution_jupiter_shadow import IsolatedBank
from execution_jupiter_read import JupiterExecutionReader
from execution_jupiter import JupiterTransactionBuilder
from execution_state import timestamp, load_config
from mykonos_meme_scout.jupiter_prepare import USDC
from mykonos_meme_scout.jupiter_simulation import associated, TOKEN


@pytest.mark.parametrize('restart',[False,True,'frozen_exit'])
def test_candidate_market_quote_route_risk_claim_unsigned_position_exit_closed(tmp_path, monkeypatch,restart):
    bank=IsolatedBank();bank.balance=5_000_000_000
    bank.accounts[bank.wallet]['lamports']=bank.balance
    bank.accounts[bank.vault_a]['lamports']=10**14
    cfg=load_config()
    store=SimpleNamespace(cfg=cfg,clock=timestamp,status=lambda:dict(
        wallet=bank.wallet,wallet_balance_lamports=str(bank.balance),reconciliation_required=False))
    reader=JupiterExecutionReader(store,fetch_build=bank.build,rpc_reader=bank.rpc,dex='Whirlpool')
    transport=RealityTransport.__new__(RealityTransport)
    transport.cfg=cfg;transport.wallet=bank.wallet;transport.builder=JupiterTransactionBuilder(store,rpc_reader=bank.rpc)
    def quote(side,mint,amount):
        # Replace READ transport only; retain production account/route checks.
        result=reader.quote(dict(side=side,mint=mint,amount_raw=str(amount),event_id='fixture'))
        result['quoted_at']=time.time()
        return result
    transport.quote=quote
    monkeypatch.setattr(module,'authority_off',lambda:cfg)
    monkeypatch.setattr(runtime,'public_wallet',lambda:bank.wallet)
    monkeypatch.setattr(runtime,'scan_once',lambda *a,**k:[])
    monkeypatch.setattr(runtime,'fx_quote',lambda:dict(source='ISOLATED_MARKET_FIXTURE',
        requested_at=time.time(),received_at=time.time(),sol_eur_bid='200',sol_eur_ask='200',usd_per_eur_mid='1'))
    source=tmp_path/'market.db';monkeypatch.setattr(runtime,'SOURCE',source)
    at=time.time()
    security=SecuritySignals(checked=True,mint_authority_disabled=True,freeze_authority_disabled=True,
        honeypot=False,top10_holder_pct=10,creator_holder_pct=1,metadata_mutable=False,
        non_transferable=False,transfer_hook_present=False,malicious_creator=False)
    def snap(when,price):
        security.checked_at=datetime.fromtimestamp(when,timezone.utc).isoformat()
        return asdict(TokenSnapshot(address=USDC,symbol='FIXTURE',name='Isolated fixture',pair_address=bank.pool,
            dex_id='orca',price_usd=price,liquidity_usd=200000,market_cap_usd=500000,
            volume_5m_usd=50000,volume_1h_usd=200000,buys_5m=1000,sells_5m=10,
            price_change_5m_pct=60,pair_created_at_ms=int((at-180)*1000),
            captured_at=datetime.fromtimestamp(when,timezone.utc).isoformat(),profile_present=True,
            security=security))
    prior,current=snap(at-1,1.6),snap(at,1.7)
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE snapshots(id INTEGER PRIMARY KEY,address TEXT,payload_json TEXT)')
        c.executemany('INSERT INTO snapshots VALUES(?,?,?)',[(1,USDC,json.dumps(prior)),(2,USDC,json.dumps(current))])
    book=RealityBook(tmp_path/'shadow.db')
    try:
        policy=runtime.frozen_policy('isolated-runtime-fixture')
        if restart=='frozen_exit':policy['exit']['maximum_hold_seconds']=1
        run=book.start('15m',policy)
        with book.transaction():run=book.get(run['run_id']);run['checkpoint']=1;book.save(run)
        run=book.tick(run['run_id'])
        runtime.trade_step(book,run,transport)
        position=book.open_trade(run['run_id'])
        assert position and position['execution_state']=='POSITION_OPEN',book.status()['last_diagnostics']
        bank.tokens=int(position['remaining_raw'])
        bank.token_account(associated(bank.wallet,USDC,TOKEN),USDC,bank.wallet,bank.tokens)
        if restart is True:
            book.close();book=RealityBook(tmp_path/'shadow.db')
            run=book.recover(run['run_id'])
            assert run['status']=='FINISHING'
        elif restart=='frozen_exit':
            monkeypatch.setattr(runtime,'POLICY',dict(policy['exit'],maximum_hold_seconds=10000,
                hard_stop_pct=99,take_profit_pct=10000,trailing_arm_pct=10000))
            run=book.get(run['run_id'])
        else:
            run=book.control('stop',run['run_id'])
        runtime.trade_step(book,run,transport)
        assert book.status()['metrics']['closed']==1
        if restart=='frozen_exit':
            assert book.trades(run['run_id'])[0]['exit_reason']=='maximum_hold'
            book.control('stop',run['run_id'])
        assert book.status()['open_position'] is None
        assert book.finish(run['run_id'])['status']=='COMPLETED'
        assert bank.sent==[] and all(not e['signing'] for e in book.trades(run['run_id'])[0]['receipts'])
    finally:book.close()


def test_session_cap_stops_entries_until_user_duration_without_early_finish(tmp_path,monkeypatch):
    b=RealityBook(tmp_path/'cap.db')
    policy=dict(position_fraction='.1',reserve_eur='0',max_session_trades=3,wallet='fixture')
    run=b.start('8h',policy);run=b.tick(run['run_id'])
    # Existing verified closure accounting is covered by the real graph tests;
    # here only the dispatcher admission count's transport is replaced.
    monkeypatch.setattr(b,'trades',lambda rid:[{'execution_state':'CLOSED'}]*3)
    monkeypatch.setattr(b,'open_trade',lambda rid:None)
    runtime.trade_step(b,run,None)
    current=b.get(run['run_id'])
    assert current['status']=='RUNNING' and not current['stop_requested']
    assert current['entry_stop_reason']=='SESSION_TRADE_LIMIT'
    assert current['planned_end']==run['started_at']+8*3600
    b.close()
