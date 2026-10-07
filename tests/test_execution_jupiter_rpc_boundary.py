"""Same-bank inventory and last SEND boundary, isolated HTTP fixtures only."""
import copy
import io
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'CORE'))
sys.path.insert(0,str(Path(__file__).parents[1]/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
import execution_evidence as evidence
import rpc_read_worker as worker
import execution_jupiter as send
import execution_policy
import execution_state
from mykonos_meme_scout.jupiter_read import JupiterSolanaEvidence

WALLET='11111111111111111111111111111111'


def replies(slots=(42,42,41)):
    return [{'jsonrpc':'2.0','id':i+1,'result':{'context':{'slot':slot},'value':1000 if i==2 else []}}
            for i,slot in enumerate(slots)]


def fixture_http(monkeypatch,bodies):
    class Connection:
        def __init__(self,*a,**k):self.calls=[];self.closed=False;connections.append(self)
        def request(self,method,path,body,headers):self.calls.append(json.loads(body))
        def getresponse(self):return SimpleNamespace(status=200,read=lambda limit:json.dumps(bodies.pop(0)).encode())
        def close(self):self.closed=True
    connections=[]
    monkeypatch.setattr(worker.http.client,'HTTPSConnection',Connection)
    monkeypatch.setattr(worker.time,'sleep',lambda seconds:None)
    return connections


def request(allowed=True,minimum=0):
    return {'url':'https://fixture','timeout':1,'payload':{'operation':'wallet_snapshot_batch',
            'wallet':WALLET,'minimum_slot':minimum,'balance_retry_allowed':allowed}}


def test_complete_batch_reorders_ids_and_corrects_balance_on_same_connection(monkeypatch):
    values=replies();connections=fixture_http(monkeypatch,[list(reversed(values)),replies()[2] | {
        'result':{'context':{'slot':42},'value':1000}}])
    result=worker.snapshot(request())
    assert result['balance_retry_used'] is True
    assert [v['result']['context']['slot'] for v in result['snapshot_responses']]==[42,42,42]
    calls=connections[0].calls
    assert len(connections)==1 and connections[0].closed and len(calls)==2
    assert calls[0][1]['method']=='getProgramAccounts'
    assert calls[0][1]['params'][1]['withContext'] is True
    assert calls[0][1]['params'][1]['filters']==[{'memcmp':{'offset':32,'bytes':WALLET}}]
    assert calls[1]['method']=='getBalance' and calls[1]['params'][1]['minContextSlot']==42


@pytest.mark.parametrize('kind',['duplicate-id','below-floor','bool-slot','string-balance','huge-balance',
                                 'missing-budget','invalid-inventory','mixed-errors'])
def test_invalid_batch_never_performs_extra_read(monkeypatch,kind):
    values=replies();message=request()
    if kind=='duplicate-id':values[1]['id']=1
    if kind=='below-floor':message['payload']['minimum_slot']=42
    if kind=='bool-slot':values[0]['result']['context']['slot']=True
    if kind=='string-balance':values[2]['result']['value']='1000'
    if kind=='huge-balance':values[2]['result']['value']=2**64
    if kind=='missing-budget':del message['payload']['balance_retry_allowed']
    if kind=='invalid-inventory':values[1]['result']['value']={}
    if kind=='mixed-errors':values[0]={'jsonrpc':'2.0','id':1,'error':{'code':-32005}}
    connections=fixture_http(monkeypatch,[values])
    if kind=='mixed-errors':assert worker.snapshot(message)['balance_retry_used'] is False
    else:
        with pytest.raises((ValueError,KeyError,TypeError)):worker.snapshot(message)
    assert sum(len(c.calls) for c in connections)<=1


@pytest.mark.parametrize('extra_slot',[42,43,41])
def test_extra_balance_slot_must_equal_tokens_or_discard_whole_round(monkeypatch,extra_slot):
    connections=fixture_http(monkeypatch,[replies(),{'jsonrpc':'2.0','id':3,'result':{
        'context':{'slot':extra_slot},'value':1000}}])
    if extra_slot<42:
        with pytest.raises(ValueError,match='context_below_floor'):worker.snapshot(request())
    else:
        result=worker.snapshot(request())
        assert result['balance_retry_used'] is True
        assert result['snapshot_responses'][2]['result']['context']['slot']==extra_slot
    assert len(connections[0].calls)==2


def test_parent_consumes_extra_read_once_discards_all_old_round_data(monkeypatch):
    bodies=[{'snapshot_responses':replies((42,42,43)),'balance_retry_used':True},
            {'snapshot_responses':replies((44,44,44)),'balance_retry_used':False}]
    calls=[]
    def read(self,payload,deadline):calls.append((copy.deepcopy(payload),deadline));return json.dumps(bodies.pop(0)).encode()
    monkeypatch.setattr(evidence.SolanaEvidence,'_request_once',read)
    rpc=JupiterSolanaEvidence(execution_state.load_config())
    assert rpc.wallet_snapshot(WALLET)==({},{},'1000',44)
    assert [p['balance_retry_allowed'] for p,d in calls]==[True,False]
    assert [p['minimum_slot'] for p,d in calls]==[0,43]
    assert calls[0][1]==calls[1][1]


@pytest.mark.parametrize('used',[None,1,'true'])
def test_missing_or_invalid_retry_receipt_is_terminal(monkeypatch,used):
    data={'snapshot_responses':replies((42,42,42))}
    if used is not None:data['balance_retry_used']=used
    calls=[]
    def read(*args):calls.append(1);return json.dumps(data).encode()
    monkeypatch.setattr(evidence.SolanaEvidence,'_request_once',read)
    with pytest.raises(evidence.StateError,match='retry_budget'):
        JupiterSolanaEvidence(execution_state.load_config()).wallet_snapshot(WALLET)
    assert len(calls)==1


@pytest.mark.parametrize('during_connect',['gate','config','expired','quote','none'])
def test_tls_connect_cannot_hide_gate_flip_or_expired_quote(monkeypatch,during_connect):
    cfg={'signing':True,'broadcast_enabled':True,'live_trading_enabled':True}
    gate={'value':'ON','valid':True};expired=[False];calls=[]
    contract={'evidenceDomain':'FINALIZED_CHAIN','expiresAtMs':time.time()*1000+10000}
    event={'evidence_domain':'FINALIZED_CHAIN','signed_transaction':'AQ=='}
    def fresh():
        if expired[0]:raise ValueError('expired_quote')
    from mykonos_meme_scout import jupiter_rpc_session
    monkeypatch.setattr(jupiter_rpc_session,'current_rpc_session',lambda:SimpleNamespace(fresh=fresh))
    monkeypatch.setattr(execution_policy,'owner_gate',lambda:dict(gate))
    monkeypatch.setattr(execution_state,'load_config',lambda:dict(cfg))
    class Connection:
        def __init__(self,*a,**k):pass
        def connect(self):
            calls.append('connect')
            if during_connect=='gate':gate['value']='OFF'
            if during_connect=='config':cfg['signing']=False
            if during_connect=='expired':contract['expiresAtMs']=0
            if during_connect=='quote':expired[0]=True
        def request(self,*a,**k):calls.append('signed_request')
        def getresponse(self):return SimpleNamespace(status=200,read=lambda n:b'{"jsonrpc":"2.0","id":1,"result":"signature"}')
        def close(self):calls.append('close')
    import http.client
    monkeypatch.setattr(http.client,'HTTPSConnection',Connection)
    send._SEND_SCOPE.permit=(contract,event)
    params=['AQ==',{'encoding':'base64','skipPreflight':False,'preflightCommitment':'finalized','maxRetries':0}]
    if during_connect=='none':assert send.send_rpc_once('sendTransaction',params)=='signature'
    else:
        with pytest.raises((execution_state.StateError,ValueError)):send.send_rpc_once('sendTransaction',params)
    assert ('signed_request' in calls)==(during_connect=='none')
    assert calls[-1]=='close' and send._SEND_SCOPE.permit is None
    with pytest.raises(execution_state.StateError,match='owned_send_scope_required'):
        send.send_rpc_once('sendTransaction',params)
