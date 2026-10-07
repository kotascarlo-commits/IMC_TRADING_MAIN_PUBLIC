"""Real process handoff with dummy SDK authentication, never real orders."""
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_execution import flow
from test_execution_adapter import ready
import test_execution_adapter as adapter_fixtures
from test_single_swap_transport import SDK, TOKEN, payload
from execution_adapter import SingleSendAdapter, SOL
from execution_state import Ledger
from execution_transport import SDKContractTransport


def test_production_host_off_never_imports_sdk_or_config(tmp_path):
    import hashlib
    import time
    gate=tmp_path/'gate.json'
    gate.write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    body=payload()
    now=int(time.time()*1000)
    contract={'eventId':'isolated','requestId':'request','tradeId':'trade','payloadJson':body,
        'payloadHash':hashlib.sha256(body.encode()).hexdigest(),'issuedAtMs':now,
        'expiresAtMs':now+1900,'responseUntilMs':now+10000}
    transport=SDKContractTransport(gate,sdk=tmp_path/'sdk-must-not-be-loaded')
    def forbidden():
        pytest.fail('OFF must not reach SDK auth or parent send authorization')
    assert transport.send_with_guard(contract,send_guard=forbidden)['state']=='NO_BROADCAST'
    assert transport.last_send_started is False


@pytest.mark.parametrize('failure', ['none','risk','ipc'],
    ids=['single-ack','risk-changed-before-send','ipc-fault-after-allow'])
def test_adapter_rechecks_original_risk_after_real_child_start(flow,tmp_path,monkeypatch,failure):
    flow.now=datetime.now(timezone.utc)
    flow.now=flow.now.replace(microsecond=flow.now.microsecond//1000*1000)
    flow.store=Ledger(tmp_path/'host.db',dict(flow.store.cfg),clock=flow.clock)
    flow.store.bootstrap(flow.clock())
    assert flow.store.reconcile({'wallet':SOL,'observed_at':flow.clock(),'chain_positions':{},
        'executor_positions':{},'orders':{},'balance_lamports':'1000000000'})
    candidate,quote_fixture,equity_fixture=flow.candidate,flow.quote,adapter_fixtures.observe
    monkeypatch.setattr(flow,'candidate',lambda **kwargs:candidate(**({'mint':TOKEN}|kwargs)))
    monkeypatch.setattr(flow,'quote',lambda event,**kwargs:dict(quote_fixture(event,**kwargs),wallet=SOL))
    monkeypatch.setattr(adapter_fixtures,'observe',lambda f:dict(equity_fixture(f),wallet=SOL))
    event,quote,risk,probes=ready(flow,tmp_path,monkeypatch)
    if failure=='ipc':
        gate=tmp_path/'corrupt-before-fetch.json'
        gate.write_bytes(risk.gate_path.read_bytes())
        risk.gate_path=gate
    transport=SDKContractTransport(risk.gate_path,
        host=Path(__file__).with_name('gmgn_execution_host_fixture.mjs'),sdk=SDK)
    original=risk.send_window
    guards=[]
    invalidator=[]
    started,finished=threading.Event(),threading.Event()
    marker=Path(str(risk.gate_path)+'.sent')
    @contextmanager
    def guard(permit):
        guards.append(permit)
        if failure=='risk' and len(guards)==3:
            flow.store.block('runtime_risk_changed')
        with original(permit) as event:
            if failure=='ipc' and len(guards)==3:
                def invalidate():
                    started.set()
                    flow.store.invalidate_valuation()
                    finished.set()
                thread=threading.Thread(target=invalidate)
                invalidator.append(thread)
                thread.start()
                assert started.wait(1) and not finished.is_set()
            yield event
    monkeypatch.setattr(risk,'send_window',guard)
    adapter=SingleSendAdapter(risk,probes,contract_transport=transport)
    result=adapter.execute(event,quote)
    for thread in invalidator: thread.join(1)
    assert result['state']==('ACKNOWLEDGED' if failure=='none' else 'UNKNOWN'), (result,len(guards),transport.last_send_started)
    assert len(guards)==3 and all(p is guards[0] for p in guards)
    assert transport.last_send_started is (failure=='none')
    assert marker.exists() is (failure=='none')
    if failure=='ipc':
        assert finished.is_set()
        assert transport._unconfirmed_child is None
    assert flow.trade(event)['remaining_raw']=='0'  # SDK ACK is not a fill.
    assert adapter.execute(event,quote)['state']=='BLOCKED'
    assert len(guards)==3  # No child or new permit on the repeated call.


def test_gateway_uses_one_adapter_and_real_missing_health_stays_blocked(flow,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import imc_pure_signal_gateway as gateway
    monkeypatch.setattr(gateway,'_LEDGER',flow.store)
    for name in ('_EXECUTION','_MONITOR','_POSITIONS'):
        monkeypatch.setattr(gateway,name,None)
    with ThreadPoolExecutor(max_workers=2) as callers:
        adapters=list(callers.map(lambda _:gateway.execution_adapter(),range(4)))
    assert all(adapter is adapters[0] and adapter.store is flow.store for adapter in adapters)
    event=flow.buy()
    quote=dict(flow.quote(event),source='GMGN_ORDER_QUOTE')
    result=adapters[0].execute(event,quote)
    assert result['state']=='BLOCKED'
    assert 'heartbeat:CORE' in result['risk']['failures']
    assert 'capability:watchdog' in result['risk']['failures']
    assert adapters[0].transport.last_send_started is False
    stored=flow.store.status()['events'][0]
    assert stored['state']=='READY' and stored.get('sent_at') is None
    assert stored.get('send_started_at') is None


def test_runtime_quote_uses_existing_reader_and_missing_market_evidence_blocks_send(flow,tmp_path,monkeypatch):
    import imc_pure_signal_gateway as gateway
    from mykonos_meme_scout import paper_forward
    event,quote,risk,probes=ready(flow,tmp_path,monkeypatch)
    calls, sends, results, quotes = [], [], [], []
    entered, release = threading.Event(), threading.Event()
    def read(self,*args):
        calls.append(args)
        assert args[:2] == ('order','quote') and args[-1] == '--raw'
        entered.set()
        assert release.wait(1)
        fields=dict(zip(args[2:-1:2],args[3:-1:2]))
        return {'input_token':fields['--input-token'],'output_token':fields['--output-token'],
            'input_amount':fields['--amount'],'output_amount':'1000','min_output_amount':'900','slippage':1}
    monkeypatch.setattr(gateway,'_LEDGER',flow.store)
    monkeypatch.setattr(gateway,'_POSITIONS',None)
    monkeypatch.setattr(gateway,'_QUOTE_RETRY_AT',0)
    monkeypatch.setattr(gateway,'DB',tmp_path/'absent-discovery.db')
    monkeypatch.setattr(gateway,'_EXECUTION',SingleSendAdapter(risk,probes,transport=lambda body:sends.append(body)))
    monkeypatch.setattr(gateway.GMGNReader,'read',read)
    monkeypatch.setattr(paper_forward,'now',lambda:flow.now.timestamp())
    original_quote=gateway.quote_event
    def capture(event):
        quoted=original_quote(event)
        quotes.append(quoted)
        return quoted
    monkeypatch.setattr(gateway,'quote_event',capture)
    worker=threading.Thread(target=lambda:results.append(gateway.dispatch_next()))
    worker.start()
    try:
        assert entered.wait(1)
        assert gateway.dispatch_next()['execution']['state']=='BUSY'
    finally:
        release.set()
        worker.join(timeout=2)
    assert not worker.is_alive() and len(calls)==1 and not sends
    assert results[0]['execution']['state']=='BLOCKED'
    assert 'state_or_evidence_unknown' in results[0]['execution']['risk']['failures']
    assert quotes[0]['quoted_at']==flow.clock() and quotes[0]['event_id']==event['event_id']
    assert quotes[0]['wallet']=='wallet' and quotes[0]['input_raw']=='100000000'
    assert quotes[0]['minimum_output_raw']=='900' and quotes[0]['slippage_bps']=='100'
    assert all(quotes[0][key] is None for key in ('price_impact_bps','price_deviation_bps','liquidity_usd'))
    stored=flow.store.status()['events'][0]
    assert stored['state']=='READY' and stored['request_id']==event['request_id']
    assert stored.get('payload_hash') is None and stored.get('sent_at') is None
    assert stored.get('send_started_at') is None


def test_runtime_quote_failure_has_bounded_backoff_and_preserves_ready_intent(flow,tmp_path,monkeypatch):
    import imc_pure_signal_gateway as gateway
    event=flow.buy()
    reads=[]
    def unavailable(self,*args):
        reads.append(args)
        raise RuntimeError('SECRET must never enter execution diagnostics')
    monkeypatch.setattr(gateway,'_LEDGER',flow.store)
    monkeypatch.setattr(gateway,'_POSITIONS',None)
    monkeypatch.setattr(gateway,'_QUOTE_RETRY_AT',0)
    monkeypatch.setattr(gateway,'DB',tmp_path/'absent-discovery.db')
    monkeypatch.setattr(gateway.GMGNReader,'read',unavailable)
    first=gateway.dispatch_next()
    second=gateway.dispatch_next()
    assert first['execution']=={'state':'BLOCKED','reason':'QUOTE_UNAVAILABLE'}
    assert second['execution']=={'state':'BLOCKED','reason':'QUOTE_BACKOFF'}
    assert len(reads)==1 and flow.store.status()['events'][0]['state']=='READY'
    assert flow.store.status()['events'][0]['request_id']==event['request_id']
    assert flow.store.status()['events'][0].get('send_started_at') is None
    assert 'SECRET' not in json.dumps([first,second,flow.store.status()])


def test_runtime_poll_busy_keeps_existing_pending_and_real_health_blocked(flow,tmp_path,monkeypatch):
    import imc_pure_signal_gateway as gateway
    from execution_positions import PositionManager
    from execution_state import StateError
    event=flow.buy()
    manager=PositionManager(flow.store)
    monkeypatch.setattr(gateway,'_LEDGER',flow.store)
    monkeypatch.setattr(gateway,'_POSITIONS',manager)
    monkeypatch.setattr(gateway,'_MONITOR',None)
    monkeypatch.setattr(gateway,'_EXECUTION',None)
    monkeypatch.setattr(gateway,'DB',tmp_path/'absent-discovery.db')
    def forbidden(event):
        pytest.fail('Busy PM must not fall through to quote or SEND')
    monkeypatch.setattr(gateway,'quote_event',forbidden)
    manager._lock.acquire()
    try:
        assert gateway.dispatch_next()=={'signal':None,'execution':{'state':'BUSY','reason':'POSITION_MANAGER_BUSY'}}
        probe = gateway.execution_probes('BUY')['heartbeats']['POSITION_MANAGER']
        assert probe['status'] == 'UNKNOWN'
        assert probe['reason'] == 'busy'
        assert flow.store.pending()==event
    finally:
        manager._lock.release()
    def failed():
        raise StateError('position_manager_deadline')
    monkeypatch.setattr(manager,'tick',failed)
    with pytest.raises(StateError,match='position_manager_deadline'):
        gateway.dispatch_next()


@pytest.mark.parametrize('seconds,origin', [(120,'provider_response'),(-1,'provider_response'),
                                          (120,'local_cooldown'),(-1,'local_cooldown')])
def test_runtime_preserves_provider_retry_deadline_and_never_counts_local_wait_as_failure(
        flow,tmp_path,monkeypatch,seconds,origin):
    import imc_pure_signal_gateway as gateway
    from mykonos_meme_scout.paper_forward import GMGNReadError
    from test_gmgn_read_evidence import evidence, stderr
    event=flow.buy()
    value=evidence(retry_after_seconds=seconds,origin=origin)
    if origin=='local_cooldown':
        value.update(http_status=None,api_code=None,quota_limit=None,quota_remaining=None,reset_source='unknown')
    clock=[100.]
    reads,failures=[],[]
    def unavailable(event):
        reads.append(event['event_id'])
        raise GMGNReadError.from_stderr(stderr(value))
    monkeypatch.setattr(gateway,'_LEDGER',flow.store)
    monkeypatch.setattr(gateway,'_QUOTE_RETRY_AT',0)
    monkeypatch.setattr(gateway,'next_event',lambda:event)
    monkeypatch.setattr(gateway,'quote_event',unavailable)
    monkeypatch.setattr(gateway.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(flow.store,'api_failure',lambda reason:failures.append(reason))
    first=gateway.dispatch_next()
    assert first['execution']['read_evidence']==value
    assert len(failures)==(1 if origin=='provider_response' else 0)
    clock[0]+=119 if seconds!=-1 else 1000000
    assert gateway.dispatch_next()['execution']['reason']=='QUOTE_BACKOFF'
    assert reads==[event['event_id']]
    assert flow.store.status()['events'][0]['state']=='READY'
    assert flow.store.status()['events'][0].get('send_started_at') is None
