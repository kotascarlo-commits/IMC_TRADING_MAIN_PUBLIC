"""Synthetic signed receipts with disposable test keys; no real wallet or RPC.

These tests prove receipt binding/accounting, not live route/send acceptance.
"""
import base64
import copy
import hashlib
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.hash import Hash
from solders.instruction import Instruction,AccountMeta
from solders.keypair import Keypair
from solders.message import MessageV0,to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_jupiter_evidence import JupiterChainEvidence,verify_receipt,binding,keys_digest,SOURCE
from execution_state import StateError,Ledger,canonical
from mykonos_meme_scout.jupiter_simulation import associated,TOKEN,SOL,message_keys

USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


def fixture(sell=False,lookup=False):
    key=Keypair();wallet=str(key.pubkey())
    ata=associated(wallet,USDC,TOKEN);wsol=associated(wallet,SOL,TOKEN);pool=str(Pubkey.new_unique())
    accounts=[wallet,ata,wsol,pool]
    tables=[AddressLookupTableAccount(Pubkey.new_unique(),[Pubkey.from_string(pool)])] if lookup else []
    ix=Instruction(Pubkey.new_unique(),b'offline receipt fixture',[
        AccountMeta(Pubkey.from_string(k),i==0,True) for i,k in enumerate(accounts)])
    message=MessageV0.try_compile(key.pubkey(),[ix],tables,Hash.new_unique())
    tx=VersionedTransaction(message,[key]);signature=str(tx.signatures[0])
    unsigned=VersionedTransaction.populate(message,[Signature.default()])
    keys,loaded=message_keys(unsigned,tables)
    pre=[0]*len(keys);post=pre.copy();pre[0]=100000000
    index=keys.index(ata);pool_index=keys.index(pool)
    def token(amount):return {'accountIndex':index,'owner':wallet,'mint':USDC,'programId':TOKEN,
        'uiTokenAmount':{'amount':str(amount),'decimals':6}}
    if sell:
        pre[index]=post[index]=2000000;post[0]=101995000
        pre[pool_index]=20000000;post[pool_index]=18000000
        before=[token(1100000)];after=[token(100000)];minimum='1900000'
    else:
        post[0]=96995000;post[index]=2000000;post[pool_index]=1000000
        before=[];after=[token(2000000)];minimum='1900000'
    block=int(time.time())-1;sent=datetime.fromtimestamp(block,timezone.utc).isoformat()
    contract={'provider':'JUPITER_V2','contract_version':2,'signature':signature,'wallet':wallet,
        'message_sha256':hashlib.sha256(to_bytes_versioned(message)).hexdigest(),'account_keys_sha256':keys_digest(keys),
        'input_token':USDC if sell else SOL,'output_token':SOL if sell else USDC,
        'input_raw':'1000000','minimum_output_raw':minimum}
    event={'provider':'JUPITER_V2','adapter_contract_version':2,'jupiter_binding':contract,
        'event_id':'test:event','request_id':'test:request','trade_id':'test:trade','wallet':wallet,'mint':USDC,
        'side':'SELL' if sell else 'BUY','order_id':signature,'actual_input_raw':'1000000',
        'sent_at':sent,'send_started_at':sent,'quote':{'source':'JUPITER_V2_ORDER_QUOTE_OBSERVATION','minimum_output_raw':minimum}}
    payload=canonical({k:v for k,v in contract.items() if k!='signature'})
    event.update(request_payload_json=payload,payload_hash=hashlib.sha256(payload.encode()).hexdigest(),signature_bound_at=sent)
    transaction={'version':0,'transaction':[base64.b64encode(bytes(tx)).decode(),'base64'],
        'slot':123,'blockTime':block,'meta':{'err':None,'fee':5000,'preBalances':pre,'postBalances':post,
        'preTokenBalances':before,'postTokenBalances':after,'loadedAddresses':loaded}}
    cfg={'max_order_fee_lamports':'3000000','request_timeout_seconds':30,'reconciliation_max_age_seconds':15}
    return event,transaction,cfg


@pytest.mark.parametrize('sell',[False,True])
@pytest.mark.parametrize('lookup',[False,True])
def test_receipt_exactsig_message_alt_and_native_net_fees_counted_once(sell,lookup):
    event,tx,cfg=fixture(sell,lookup)
    proof=verify_receipt(event,tx,cfg)
    assert proof['source']==SOURCE and proof['provider']=='JUPITER_V2'
    assert proof['wallet_delta_lamports']==('1995000' if sell else '-3005000')
    assert proof['fees_lamports']=='5000' and proof['swap_input_raw']=='1000000'
    assert proof['swap_output_raw']=='2000000' and proof['order_terminal'] is True
    assert proof['commitment']=='finalized'


@pytest.mark.parametrize('change',['unbound','provider','contract_version','order_id','message','keys',
    'zero_signature','unsigned_wire','wrong_wire','failed','fee','bool_fee','version','bool_version',
    'missing_native','conservation','below_minimum','foreign_owner','missing_tokens',
    'duplicate_token','existing_wsol','slot','block_time','historical','identity_change','unexpected_asset',
    'payload_hash','late_binding','missing_bind_clock','invalid_signature'])
def test_unrelated_or_corrupt_receipts_cannot_complete_attempt(change):
    event,tx,cfg=fixture();meta=tx['meta'];contract=event['jupiter_binding']
    if change=='unbound':event.pop('jupiter_binding')
    if change=='provider':event['provider']='GMGN'
    if change=='contract_version':event['adapter_contract_version']=1
    if change=='order_id':event['order_id']=str(Signature.new_unique())
    if change=='message':contract['message_sha256']='0'*64
    if change=='keys':contract['account_keys_sha256']='0'*64
    if change=='zero_signature':contract['signature']=event['order_id']=str(Signature.default())
    if change=='unsigned_wire':
        decoded=VersionedTransaction.from_bytes(base64.b64decode(tx['transaction'][0]))
        tx['transaction'][0]=base64.b64encode(bytes(VersionedTransaction.populate(decoded.message,[Signature.default()]))).decode()
    if change=='wrong_wire':tx['transaction'][0]='AA=='
    if change=='failed':meta['err']={}
    if change=='fee':meta['fee']=3000001
    if change=='bool_fee':meta['fee']=True
    if change=='version':tx['version']=1
    if change=='bool_version':tx['version']=False
    if change=='missing_native':meta.pop('preBalances')
    if change=='conservation':meta['postBalances'][0]+=1
    if change=='below_minimum':meta['postTokenBalances'][0]['uiTokenAmount']['amount']='1800000'
    if change=='foreign_owner':meta['postTokenBalances'][0]['owner']=str(Pubkey.new_unique())
    if change=='missing_tokens':meta.pop('postTokenBalances')
    if change=='duplicate_token':meta['postTokenBalances']*=2
    if change=='existing_wsol':
        decoded=VersionedTransaction.from_bytes(base64.b64decode(tx['transaction'][0]))
        i=[str(k) for k in decoded.message.account_keys].index(associated(event['wallet'],SOL,TOKEN))
        meta['preBalances'][i]=1;meta['preBalances'][0]-=1
    if change=='slot':tx['slot']=True
    if change=='block_time':tx['blockTime']=int(time.time())+60
    if change=='historical':tx['blockTime']-=100
    if change=='identity_change':meta['preTokenBalances']=[dict(meta['postTokenBalances'][0],owner=str(Pubkey.new_unique()))]
    if change=='unexpected_asset':meta['postTokenBalances'][0]['mint']=SOL
    if change=='payload_hash':event['payload_hash']='0'*64
    if change=='late_binding':event['signature_bound_at']=datetime.fromtimestamp(int(time.time())+1,timezone.utc).isoformat()
    if change=='missing_bind_clock':event.pop('signature_bound_at')
    if change=='invalid_signature':
        decoded=VersionedTransaction.from_bytes(base64.b64decode(tx['transaction'][0]));fake=Signature.new_unique()
        tx['transaction'][0]=base64.b64encode(bytes(VersionedTransaction.populate(decoded.message,[fake]))).decode()
        contract['signature']=event['order_id']=str(fake)
    with pytest.raises((StateError,ValueError)):
        verify_receipt(event,tx,cfg)


def test_unknown_after_crash_or_expiry_is_read_only_and_never_reissued():
    event,tx,cfg=fixture();calls=[]
    def transport(method,params):
        calls.append((method,params))
        return None
    rpc=SimpleNamespace(transport=transport)
    reader=JupiterChainEvidence(cfg,rpc=rpc)
    for _ in range(3):
        assert reader.observe(event)=={'state':'UNRESOLVED','order_id':event['order_id'],'provider':'JUPITER_V2'}
    assert all(method=='getTransaction' and params[1]=={'encoding':'base64','commitment':'finalized',
        'maxSupportedTransactionVersion':0} for method,params in calls)
    event.pop('send_started_at')
    with pytest.raises(StateError):reader.observe(event)
    assert len(calls)==3


def test_finalized_recovery_returns_same_identity_and_no_ledger_or_network_write():
    event,tx,cfg=fixture()
    reader=JupiterChainEvidence(cfg,rpc=SimpleNamespace(transport=lambda *a:tx))
    result=reader.observe(event)
    assert result['state']=='FILLED'
    assert result['proof']['event_id']==event['event_id']
    assert result['proof']['signature']==event['order_id']


def test_existing_ledger_rejects_jupiter_receipt_for_gmgn_claim_before_mutation():
    event,tx,cfg=fixture();proof=verify_receipt(event,tx,cfg)
    event['provider']='GMGN';event['adapter_contract_version']=1
    with pytest.raises(StateError,match='binding'):
        Ledger.apply_fill(None,None,event,None,{},proof)


def test_changed_actual_wire_message_hits_receipt_hash_check_with_consistent_binding():
    event,tx,cfg=fixture()
    decoded=VersionedTransaction.from_bytes(base64.b64decode(tx['transaction'][0]));message=decoded.message
    changed=MessageV0(message.header,message.account_keys,Hash.new_unique(),message.instructions,message.address_table_lookups)
    tx['transaction'][0]=base64.b64encode(bytes(VersionedTransaction.populate(changed,decoded.signatures))).decode()
    assert binding(event)==event['jupiter_binding']
    with pytest.raises(StateError,match='finalized_receipt'):
        verify_receipt(event,tx,cfg)


def test_changed_rpc_loaded_addresses_hit_resolved_keys_hash_not_payload_binding():
    event,tx,cfg=fixture(lookup=True)
    assert tx['meta']['loadedAddresses']['writable']
    tx['meta']['loadedAddresses']['writable'][0]=str(Pubkey.new_unique())
    assert binding(event)==event['jupiter_binding']
    with pytest.raises(StateError,match='finalized_receipt'):
        verify_receipt(event,tx,cfg)


def test_future_signature_send_clock_blocks_before_any_rpc():
    event,tx,cfg=fixture();calls=[]
    future=datetime.fromtimestamp(int(time.time())+60,timezone.utc).isoformat()
    event['signature_bound_at']=event['send_started_at']=future
    reader=JupiterChainEvidence(cfg,rpc=SimpleNamespace(transport=lambda *a:calls.append(a)))
    with pytest.raises(StateError,match='binding'):reader.observe(event)
    assert not calls


def test_chain_timestamp_cannot_predate_later_send_by_more_than_two_seconds():
    event,tx,cfg=fixture()
    event['sent_at']=datetime.fromtimestamp(tx['blockTime']-1,timezone.utc).isoformat()
    # Claim <= binding <= send <= now, yet block occurred long before the send.
    later=datetime.fromtimestamp(tx['blockTime']+5,timezone.utc).isoformat()
    event['signature_bound_at']=event['send_started_at']=later
    now=datetime.fromtimestamp(tx['blockTime']+6,timezone.utc).isoformat()
    assert binding(event,now=now)
    with pytest.raises(StateError,match='finalized_receipt'):verify_receipt(event,tx,cfg,now=now)
    event['signature_bound_at']=event['send_started_at']=datetime.fromtimestamp(tx['blockTime']+2,timezone.utc).isoformat()
    assert verify_receipt(event,tx,cfg,now=now)['order_terminal']
