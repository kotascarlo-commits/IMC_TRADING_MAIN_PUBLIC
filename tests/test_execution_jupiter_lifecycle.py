"""Same ledger/risk/adapter/builder/receipt path, isolated account transport."""
import copy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_state import Ledger, StateError, timestamp
from execution_policy import RiskEngine, CRITICAL, CAPABILITIES
from execution_adapter import SingleSendAdapter
from execution_validation import initialize, status as validation_status
from execution_jupiter import (IsolatedJupiterTransport, JupiterTransactionBuilder, JupiterRPCTransport,
                              recover_observation, bind_signature)
from execution_jupiter_evidence import JupiterChainEvidence
from execution_jupiter_read import JupiterExecutionReader
from execution_jupiter_shadow import IsolatedBank
from mykonos_meme_scout.jupiter_prepare import USDC


from execution_validation_run import IsolatedValidationRun as Harness


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path/'execution.db')


def test_same_production_path_buy_position_sell_closed_and_three_stop(harness):
    h = harness
    for count in range(1, 4):
        h.roundtrip()
        with h.store.transaction() as c:
            assert validation_status(h.store, c)['closed_count'] == count
        assert h.store.status()['performance']['count'] == 0
    assert len(h.bank.sent) == 6 and len(set(h.bank.sent)) == 6
    assert h.store.status()['open_positions'] == 0
    assert not h.store.control('resume_buys')
    assert h.store.control('reset_session')
    assert h.buy() is None
    restarted = Ledger(h.store.path)
    with restarted.transaction() as c:
        assert validation_status(restarted, c)['stopped']
        assert len(c.execute("SELECT * FROM audit WHERE action='validation_auto_stop'").fetchall()) == 1
        assert not any('owner_entry_authorization' in e for e in restarted.all(c, 'events'))


def test_ack_is_not_position_and_recovery_restores_exact_fill(harness):
    h = harness; event = h.buy(); quote = h.reader.quote(event)
    assert h.adapter.execute(event, quote)['state'] == 'ACKNOWLEDGED'
    assert h.store.status()['open_positions'] == 0
    assert h.adapter.execute(event, quote)['state'] == 'BLOCKED'
    assert len(h.bank.sent) == 1
    h.store = Ledger(h.store.path); h.store.recover(); h.attach()
    assert h.reconcile()
    assert h.store.status()['open_positions'] == 1
    assert len(h.bank.sent) == 1
    assert h.store.status()['performance']['count'] == 0


def test_production_transport_rejects_isolated_domain_before_sign_or_send(harness):
    calls = []
    transport = JupiterRPCTransport(harness.store, lambda e: calls.append('sign'), lambda *a: calls.append('send'))
    with pytest.raises(StateError, match='production_rejects_isolated'):
        transport.sign({'evidence_domain': 'ISOLATED'})
    with pytest.raises(StateError, match='production_rejects_isolated'):
        transport.send_with_guard({'evidenceDomain': 'ISOLATED'}, send_guard=lambda: None)
    assert calls == []


def test_validation_risk_failure_aborts_before_any_claim(harness):
    h=harness;event=h.buy();quote=h.reader.quote(event);quote['quoted_at']='2020-01-01T00:00:00+00:00'
    assert h.adapter.execute(event,quote)['state']=='BLOCKED'
    with h.store.transaction() as c:assert validation_status(h.store,c)['aborted']
    assert h.bank.sent==[] and not h.store.status()['open_positions']


def test_validation_read_failure_aborts_immediately(harness):
    harness.store.api_failure('jupiter_quote_stale')
    with harness.store.transaction() as c:assert validation_status(harness.store,c)['aborted']
    assert not harness.store.control('resume_buys')


class Crash(BaseException):
    pass


@pytest.mark.parametrize('point', ['before_claim', 'after_claim', 'after_build', 'before_send', 'unknown_send', 'after_ack',
                                 'after_fill', 'open_position', 'exit', 'after_sell', 'before_closed'])
def test_crash_matrix_no_duplicate_no_lost_trade_no_phantom(harness, monkeypatch, point):
    h = harness; event = h.buy(); quote = h.reader.quote(event)
    if point in {'before_claim', 'after_claim', 'after_build', 'before_send'}:
        target, name = {'before_claim': (h.risk, 'reserve'), 'after_claim': (h.builder, 'build'),
                        'after_build': (h.transport, 'sign'), 'before_send': (h.transport, 'send_with_guard')}[point]
        def crash(*args, **kwargs):raise Crash()
        monkeypatch.setattr(target, name, crash)
        with pytest.raises(Crash):h.adapter.execute(event, quote)
    elif point == 'unknown_send':
        original = h.bank.send
        def missing_ack(event):
            original(event)
            raise TimeoutError('uncertain after possible dispatch')
        monkeypatch.setattr(h.bank, 'send', missing_ack)
        assert h.adapter.execute(event, quote)['state'] == 'UNKNOWN'
    else:
        assert h.adapter.execute(event, quote)['state'] == 'ACKNOWLEDGED'
        if point == 'after_fill':
            stored = h.store.status()['events'][0]
            result = h.receipts.observe(stored)
            proof = dict(result['proof'], evidence_domain='ISOLATED')
            data = {'feedback_id': 'explicit-fill', **{k: stored[k] for k in ('event_id','trade_id','mint','side','order_id')},
                    'status': 'FILLED', 'signature': stored['order_id']}
            assert h.store.feedback(data, proof)
        if point in {'open_position', 'exit', 'after_sell', 'before_closed'}:
            assert h.reconcile(); h.equity()
        if point in {'exit', 'after_sell', 'before_closed'}:
            sell = h.store.prepare_sell(event['trade_id'])
            assert sell and sell['amount_raw'] == str(h.bank.tokens)
        if point in {'after_sell', 'before_closed'}:
            assert h.adapter.execute(sell, h.reader.quote(sell))['state'] == 'ACKNOWLEDGED'
        if point == 'before_closed':
            original = h.store.transition
            def crash_close(c, trade, state, reason, *args, **kwargs):
                if state == 'CLOSED':raise Crash()
                return original(c, trade, state, reason, *args, **kwargs)
            monkeypatch.setattr(h.store, 'transition', crash_close)
            with pytest.raises(Crash):h.reconcile()
    sends = len(h.bank.sent)
    h.store = Ledger(h.store.path); h.store.recover(); h.attach()
    result = h.reconcile()
    assert len(h.bank.sent) == sends
    with h.store.transaction() as c:
        events = h.store.all(c, 'events'); trades = h.store.all(c, 'trades')
        assert len([e for e in events if e['side'] == 'BUY']) == 1
        assert len([e for e in events if e['side'] == 'SELL']) <= 1
        assert sum(int(t['remaining_raw']) for t in trades) == h.bank.tokens
        if point == 'before_claim':
            assert result and events[0]['state'] == 'READY'
        elif point in {'after_claim', 'after_build', 'before_send'}:
            assert not result and events[0]['state'] in {'SENT','UNKNOWN'}
            assert h.store.meta(c, 'validation_abort')
        elif point in {'after_sell', 'before_closed'}:
            assert result and trades[0]['state'] == 'CLOSED'
            assert validation_status(h.store, c)['closed_count'] == 1
        else:
            assert result and trades[0]['execution_state'] == 'POSITION_OPEN'
    if point != 'before_claim':
        assert h.adapter.execute(event, quote)['state'] == 'BLOCKED'
        assert len(h.bank.sent) == sends


@pytest.mark.parametrize('change', ['mint_authority','freeze_authority','decimals','frozen_wallet','vault_owner',
                                  'pool_mint','tick_pda','tick_start','tick_sequence','route_enum','route_trailing',
                                  'plan_amount','plan_minimum','plan_pool','recipient','liquidity'])
def test_route_mutations_fail_before_claim_or_send(harness, change):
    import base64
    from solders.pubkey import Pubkey
    from mykonos_meme_scout.jupiter_route import verify, TOKEN
    from mykonos_meme_scout.jupiter_simulation import associated
    h = harness; event = h.buy(); quote = h.reader.quote(event)
    build = copy.deepcopy(quote['build']); accounts = copy.deepcopy(h.bank.accounts)
    def mutate(key, offset, content):
        body = bytearray(base64.b64decode(accounts[key]['data'][0]));body[offset:offset+len(content)] = content
        accounts[key]['data'][0] = base64.b64encode(body).decode()
    if change == 'mint_authority':mutate(USDC, 4, bytes(Pubkey.new_unique()))
    if change == 'freeze_authority':mutate(USDC, 50, bytes(Pubkey.new_unique()))
    if change == 'decimals':mutate(USDC, 44, b'\x09')
    if change == 'frozen_wallet':mutate(associated(h.bank.wallet,USDC,TOKEN),108,b'\x02')
    if change == 'vault_owner':mutate(h.bank.vault_a,32,bytes(Pubkey.new_unique()))
    if change == 'pool_mint':mutate(h.bank.pool,101,bytes(Pubkey.new_unique()))
    if change == 'tick_pda':
        old=build['swapInstruction']['accounts'][18]['pubkey'];new=str(Pubkey.new_unique())
        accounts[new]=accounts[old];build['swapInstruction']['accounts'][18]['pubkey']=new
    if change == 'tick_start':mutate(h.bank.ticks[0],8,(5632).to_bytes(4,'little',signed=True))
    if change == 'tick_sequence':build['swapInstruction']['accounts'][19],build['swapInstruction']['accounts'][20]=build['swapInstruction']['accounts'][20],build['swapInstruction']['accounts'][19]
    if change in {'route_enum','route_trailing'}:
        body=bytearray(base64.b64decode(build['swapInstruction']['data']))
        if change=='route_enum':body[34]=26
        else:body+=b'\x00'
        build['swapInstruction']['data']=base64.b64encode(body).decode()
    if change == 'plan_amount':build['routePlan'][0]['swapInfo']['inAmount']='1'
    if change == 'plan_minimum':build['otherAmountThreshold']='1'
    if change == 'plan_pool':build['routePlan'][0]['swapInfo']['ammKey']=str(Pubkey.new_unique())
    if change == 'recipient':build['swapInstruction']['accounts'][2]['pubkey']=str(Pubkey.new_unique())
    if change == 'liquidity':
        mutate(h.bank.vault_b,64,(100).to_bytes(8,'little'))
        mutate(h.bank.vault_a,64,(100).to_bytes(8,'little'))
    with pytest.raises((StateError,ValueError,TypeError,KeyError)):
        verify(build,quote['query'],accounts.get)
    assert h.bank.sent == []


def test_second_position_cannot_start_until_first_closed(harness):
    h=harness;buy=h.buy();h.leg(buy)
    assert h.buy() is None
    with h.store.transaction() as c:assert validation_status(h.store,c)['closed_count']==0


def test_duplicate_instances_share_one_claim(harness):
    from concurrent.futures import ThreadPoolExecutor
    h=harness;event=h.buy();quote=h.reader.quote(event)
    other=Ledger(h.store.path)
    transport=IsolatedJupiterTransport(other,h.bank)
    adapter=SingleSendAdapter(RiskEngine(other,isolated_transport=transport),h.probes,contract_transport=transport,
                             jupiter_builder=JupiterTransactionBuilder(other,rpc_reader=h.bank.rpc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda a:a.execute(event,quote)['state'],[h.adapter,adapter]))
    assert results.count('ACKNOWLEDGED')==1 and results.count('BLOCKED')==1
    assert len(h.bank.sent)==1


@pytest.mark.parametrize('winning_state',['ACK','SENT'])
def test_losing_instance_stale_risk_failure_cannot_abort_winning_claim(harness,monkeypatch,winning_state):
    h=harness;event=h.buy();quote=h.reader.quote(event)
    other=Ledger(h.store.path);transport=IsolatedJupiterTransport(other,h.bank)
    loser=SingleSendAdapter(RiskEngine(other,isolated_transport=transport),h.probes,
        contract_transport=transport,jupiter_builder=JupiterTransactionBuilder(other,rpc_reader=h.bank.rpc))
    def interleaved_assessment(*args):
        if winning_state=='ACK':assert h.adapter.execute(event,quote)['state']=='ACKNOWLEDGED'
        else:h.risk.reserve(event['event_id'],quote,None,provider='JUPITER_V2')
        return {'authorized':False,'ready':False,'failures':['already_claimed']}
    monkeypatch.setattr(loser,'assess',interleaved_assessment)
    assert loser.execute(event,quote)['state']=='BLOCKED'
    with other.transaction() as c:
        assert validation_status(other,c)['aborted'] is False
        assert other.meta(c,'kill_reason') is None
        assert other.get(c,'events',event['event_id'])['state']==winning_state
        assert sum(bool(e.get('sent_at')) for e in other.all(c,'events'))==1
    assert len(h.bank.sent)==(1 if winning_state=='ACK' else 0)
    if winning_state=='ACK':assert h.reconcile()


def test_failed_chain_transaction_accounts_fee_once_never_fill_or_retry(harness):
    h=harness;event=h.buy();quote=h.reader.quote(event)
    def failed(event):
        import base64
        from solders.transaction import VersionedTransaction
        tx=VersionedTransaction.from_bytes(base64.b64decode(event['signed_transaction']))
        meta=h.bank.evaluate(tx)
        meta['err']={'InstructionError':[3,'Custom']}
        meta['postBalances']=meta['preBalances'].copy();meta['postBalances'][0]-=meta['fee']
        meta['postTokenBalances']=copy.deepcopy(meta['preTokenBalances'])
        h.bank.balance-=meta['fee'];h.bank.accounts[h.bank.wallet]['lamports']=h.bank.balance
        h.bank.sent.append(event['request_id'])
        h.bank.receipts[event['order_id']]={'version':0,'transaction':[event['signed_transaction'],'base64'],
            'slot':101,'blockTime':int(time.time()),'meta':meta}
    h.bank.send=failed
    assert h.adapter.execute(event,quote)['state']=='ACKNOWLEDGED'
    assert not h.reconcile()
    state=h.store.status();trade=state['trades'][0]
    assert trade['remaining_raw']=='0' and trade['buy_raw']=='0'
    assert int(trade['fees_lamports'])>0 and int(trade['wallet_delta_lamports'])==-int(trade['fees_lamports'])
    cash=trade['wallet_delta_lamports']
    assert not h.reconcile()
    assert h.store.status()['trades'][0]['wallet_delta_lamports']==cash
    assert h.adapter.execute(event,quote)['state']=='BLOCKED' and len(h.bank.sent)==1
    with h.store.transaction() as c:assert validation_status(h.store,c)['closed_count']==0


@pytest.mark.parametrize('change',['domain','claim','request','route','receipt','message','cash','tokens'])
def test_receipt_lifecycle_identity_mismatch_stops_without_phantom_position(harness,change):
    h=harness;event=h.buy();quote=h.reader.quote(event)
    assert h.adapter.execute(event,quote)['state']=='ACKNOWLEDGED'
    stored=h.store.status()['events'][0];proof=h.receipts.observe(stored)['proof']
    field={'domain':'evidence_domain','claim':'claim_id','request':'request_id','route':'route_identity',
           'receipt':'receipt_identity','message':'message_sha256','cash':'wallet_delta_lamports','tokens':'token_delta_raw'}[change]
    proof[field]='0' if change in {'cash','tokens'} else 'wrong'
    data={'feedback_id':'corrupt-receipt',**{k:stored[k] for k in ('event_id','trade_id','mint','side','order_id')},
          'signature':stored['order_id'],'status':'FILLED'}
    try:assert not h.store.feedback(data,proof)
    except StateError:pass
    assert h.store.status()['trades'][0]['remaining_raw']=='0'


def test_real_gates_off_and_no_owned_scope_block_signing_and_rpc_before_io(harness):
    from execution_jupiter import send_rpc_once,OwnedWalletSigner
    calls=[]
    transport=JupiterRPCTransport(harness.store,lambda e:calls.append('sign'),lambda *a:calls.append('send'))
    with pytest.raises(StateError,match='real_gates_off'):
        transport.sign({'evidence_domain':'FINALIZED_CHAIN'})
    with pytest.raises(StateError,match='owned_send_scope_required'):
        send_rpc_once('sendTransaction',['AA==',{}])
    assert calls==[]
    assert harness.store.control('start_three_trade_validation') is False


def test_foreign_inventory_never_becomes_position_or_sell_quantity(harness):
    from execution_state import canonical
    from solders.pubkey import Pubkey
    h=harness;mint=str(Pubkey.new_unique());foreign={mint:'12345'}
    with h.store.transaction() as c:
        h.store.set_meta(c,'validation_foreign_inventory',foreign)
        h.store.audit(c,'validation_wallet_migration',{'foreign_inventory':foreign})
    original=h.bank.snapshot
    h.bank.snapshot=lambda clock:dict(original(clock),chain_positions={**original(clock)['chain_positions'],**foreign})
    assert h.reconcile();h.equity();h.roundtrip()
    assert h.store.status()['open_positions']==0
    assert h.bank.tokens==0
    h.bank.snapshot=lambda clock:dict(original(clock),chain_positions={mint:'12344'})
    assert not h.reconcile()
    assert h.store.status()['kill_reason']=='position_balance_disagreement'
    assert h.buy() is None
