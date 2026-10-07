"""Offline unsigned-build adversarial fixtures. No wallet decrypt or network."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from solders.hash import Hash
from solders.pubkey import Pubkey
from solders.signature import Signature
from mykonos_meme_scout import jupiter_prepare as prep
from mykonos_meme_scout import jupiter_instructions as policy
from mykonos_meme_scout.jupiter_simulation import associated, TOKEN, SOL

spec=importlib.util.spec_from_file_location("jupiter_prepare_worker_fixture",prep.WORKER)
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def query():
    return {"inputMint":prep.SOL,"outputMint":prep.USDC,"amount":"1000000",
            "taker":str(Pubkey.new_unique()),"slippageBps":100,"mode":"fast"}


def build(q):
    # Minimal deliberately controlled route bytes; full semantics are never approved.
    data=hashlib.sha256(b"global:route_v2").digest()[:8]
    data+=(1000000).to_bytes(8,"little")+(100000).to_bytes(8,"little")
    data+=(100).to_bytes(2,"little")+bytes(4)+(1).to_bytes(4,"little")+bytes([26,16,39,0,1])
    keys=[q["taker"],str(Pubkey.new_unique()),str(Pubkey.new_unique()),q["inputMint"],q["outputMint"],
          "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
          prep.JUPITER,"D8cy77BBepLMngZx6ZukaTff5hCt1HrWyKk3Hnd9oitf",prep.JUPITER]
    return {"inputMint":q["inputMint"],"outputMint":q["outputMint"],"inAmount":q["amount"],
            "outAmount":"100000","otherAmountThreshold":"99000","swapMode":"ExactIn",
            "slippageBps":100,"priceImpactPct":"0.001","routePlan":[{}],
            "swapInstruction":{"programId":prep.JUPITER,"accounts":[{"pubkey":k,"isSigner":i==0,
                "isWritable":i in (1,2)} for i,k in enumerate(keys)],"data":base64.b64encode(data).decode()}}


def test_unsigned_v2_build_wallet_mint_amount_bound():
    q=query();v=build(q)
    tx=prep.assemble(v,q,[],str(Hash.new_unique()))
    assert tx.signatures==[Signature.default()]
    assert tx.message.header.num_required_signatures==1 and str(tx.message.account_keys[0])==q["taker"]


@pytest.mark.parametrize("offset",[0,8,16,24,26,28,30])
def test_v2_data_amount_slippage_fee_discriminator_and_vector_mutations_rejected(offset):
    q=query();v=build(q);data=bytearray(base64.b64decode(v["swapInstruction"]["data"]))
    data[offset]=255;v["swapInstruction"]["data"]=base64.b64encode(data).decode()
    with pytest.raises(ValueError):prep.assemble(v,q,[],str(Hash.new_unique()))


@pytest.mark.parametrize("change",["mint","input","threshold","impact","signer","token_program","tip"])
def test_build_and_accounts_unbound_or_unknown_never_assemble(change):
    q=query();v=build(q)
    if change=="mint":v["outputMint"]=prep.SOL
    if change=="input":v["inAmount"]="1000001"
    if change=="threshold":v["otherAmountThreshold"]="98000"
    if change=="impact":v["priceImpactPct"]="NaN"
    if change=="signer":v["swapInstruction"]["accounts"][1]["isSigner"]=True
    if change=="token_program":v["swapInstruction"]["accounts"][5]["pubkey"]=prep.JUPITER
    if change=="tip":v["tipInstruction"]=v["swapInstruction"]
    with pytest.raises(ValueError):prep.assemble(v,q,[],str(Hash.new_unique()))


@pytest.mark.parametrize("method",["sendTransaction","requestAirdrop","sendRawTransaction","confirmTransaction"])
def test_rpc_send_or_write_methods_blocked_before_network(method):
    with pytest.raises(ValueError,match="read_only_rpc"):
        worker.validate({"operation":"rpc","payload":{"jsonrpc":"2.0","id":1,"method":method,"params":[]}})


@pytest.mark.parametrize("change",[{"sigVerify":True},{"replaceRecentBlockhash":True},
    {"commitment":"processed"},{"accounts":{}},{"sigVerify":0}])
def test_simulation_options_cannot_enable_signatures_or_weaken_finality(change):
    opts={"encoding":"base64","sigVerify":False,"replaceRecentBlockhash":False,"commitment":"finalized"}|change
    with pytest.raises(ValueError):worker.validate({"operation":"rpc","payload":{
        "jsonrpc":"2.0","id":1,"method":"simulateTransaction","params":["AA==",opts]}})


def test_prepare_worker_uses_base_interpreter_and_entire_lifetime_budget(monkeypatch):
    def timeout(command,**kw):
        assert Path(command[0])==Path(prep.sys._base_executable).resolve()
        assert 0<kw["timeout"]<=8
        raise prep.subprocess.TimeoutExpired(command,kw["timeout"])
    monkeypatch.setattr(prep.subprocess,"run",timeout)
    with pytest.raises(prep.subprocess.TimeoutExpired):prep.worker({"operation":"build","query":query()},time.monotonic()+20)


def test_prepare_429_is_typed_for_durable_shared_cooldown(monkeypatch):
    monkeypatch.setattr(prep.subprocess,"run",lambda *a,**kw:SimpleNamespace(
        returncode=1,stdout=json.dumps({"error_class":"HTTP_STATUS","http_status":429})))
    with pytest.raises(prep.JupiterReadError,match="RATE_LIMITED"):
        prep.worker({"operation":"build","query":query()},time.monotonic()+20)


def canonical_build():
    q=query();v=build(q);wallet=q["taker"]
    source=associated(wallet,prep.SOL,TOKEN);destination=associated(wallet,prep.USDC,TOKEN)
    def ix(program,data,keys):
        return {"programId":program,"data":base64.b64encode(data).decode(),"accounts":[
            {"pubkey":k,"isSigner":s,"isWritable":w} for k,s,w in keys]}
    v["swapInstruction"]["accounts"][1]["pubkey"]=source
    v["swapInstruction"]["accounts"][2]["pubkey"]=destination
    def ata(mint,address):return ix(policy.ATA,b'\x01',[(wallet,True,True),(address,False,True),
        (wallet,False,False),(mint,False,False),(policy.SYSTEM,False,False),(TOKEN,False,False)])
    v["setupInstructions"]=[ata(prep.SOL,source),ix(policy.SYSTEM,(2).to_bytes(4,'little')+
        (1000000).to_bytes(8,'little'),[(wallet,True,True),(source,False,True)]),
        ix(TOKEN,b'\x11',[(source,False,True)]),ata(prep.USDC,destination)]
    v["computeBudgetInstructions"]=[ix(policy.BUDGET,b'\x03'+(100000).to_bytes(8,'little'),[])]
    v["cleanupInstruction"]=ix(TOKEN,b'\x09',[(source,False,True),(wallet,False,True),(wallet,True,False)])
    return q,v


def test_auxiliary_wallet_transfer_associated_accounts_and_refund_are_bound():
    q,v=canonical_build();result=policy.validate_auxiliary(v,q)
    assert result['auxiliary_instruction_policy_verified'] is True
    assert result['compute_unit_price_micro_lamports']==100000
    assert result['full_route_semantics_verified'] is False


@pytest.mark.parametrize('change',['program','transfer_amount','transfer_recipient','refund',
    'duplicate_ata','ata_owner','ata_mint','extra_instruction','compute_limit','compute_duplicate',
    'tip','swap_recipient','optional_destination','sync_order','cleanup_missing','token2022'])
def test_unknown_or_redirected_auxiliary_instructions_block(change):
    q,v=canonical_build()
    if change=='program':v['setupInstructions'][0]['programId']=prep.JUPITER
    if change=='transfer_amount':v['setupInstructions'][1]['data']=base64.b64encode((2).to_bytes(4,'little')+(1000001).to_bytes(8,'little')).decode()
    if change=='transfer_recipient':v['setupInstructions'][1]['accounts'][1]['pubkey']=str(Pubkey.new_unique())
    if change=='refund':v['cleanupInstruction']['accounts'][1]['pubkey']=str(Pubkey.new_unique())
    if change=='duplicate_ata':v['setupInstructions'].append(v['setupInstructions'][0])
    if change=='ata_owner':v['setupInstructions'][0]['accounts'][2]['pubkey']=str(Pubkey.new_unique())
    if change=='ata_mint':v['setupInstructions'][0]['accounts'][3]['pubkey']=str(Pubkey.new_unique())
    if change=='extra_instruction':v['otherInstructions']=[v['setupInstructions'][1]]
    if change=='compute_limit':v['computeBudgetInstructions'][0]['data']=base64.b64encode(b'\x02'+(100000).to_bytes(4,'little')).decode()
    if change=='compute_duplicate':v['computeBudgetInstructions']*=2
    if change=='tip':v['tipInstruction']=v['setupInstructions'][1]
    if change=='swap_recipient':v['swapInstruction']['accounts'][2]['pubkey']=str(Pubkey.new_unique())
    if change=='optional_destination':v['swapInstruction']['accounts'][7]['pubkey']=str(Pubkey.new_unique())
    if change=='sync_order':v['setupInstructions'][1:3]=reversed(v['setupInstructions'][1:3])
    if change=='cleanup_missing':v.pop('cleanupInstruction')
    if change=='token2022':v['swapInstruction']['accounts'][6]['pubkey']='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
    with pytest.raises(ValueError):policy.validate_auxiliary(v,q)


def test_shared_v2_wallet_and_both_associated_accounts_bound():
    q,v=canonical_build();a=v['swapInstruction']['accounts']
    shared=[{'pubkey':str(Pubkey.new_unique()),'isSigner':False,'isWritable':False},
        a[0],a[1],copy.deepcopy(a[1]),copy.deepcopy(a[2]),a[2],*a[3:7],a[8],a[9]]
    v['swapInstruction']['accounts']=shared
    v['swapInstruction']['data']=base64.b64encode(hashlib.sha256(b'global:shared_accounts_route_v2').digest()[:8]+b'\x00').decode()
    assert policy.validate_auxiliary(v,q)['swap_associated_accounts_bound']
    v['swapInstruction']['accounts'][5]['pubkey']=str(Pubkey.new_unique())
    with pytest.raises(ValueError,match='recipient'):policy.validate_auxiliary(v,q)


@pytest.mark.parametrize('failure',[None,'second_units','second_error','replacement','fee_unknown','first_error'])
def test_two_phase_rebuild_remeasure_exact_new_unsigned_message(monkeypatch,failure):
    q,v=canonical_build();limits=[];messages=[];simulations=[]
    def assembler(blockhash,limit):
        limits.append(limit)
        return prep.assemble(v,q,[],blockhash,compute_unit_limit=limit)
    def fake_rpc(method,params,deadline):
        assert deadline>time.monotonic()
        if method=='getLatestBlockhash':return {'value':{'blockhash':str(Hash.new_unique()),'lastValidBlockHeight':999}}
        if method=='getFeeForMessage':
            messages.append(base64.b64decode(params[0]))
            return {'value':None if failure=='fee_unknown' else 5000}
        assert method=='simulateTransaction'
        assert params[1]=={'encoding':'base64','sigVerify':False,'replaceRecentBlockhash':False,'commitment':'finalized'}
        from solders.transaction import VersionedTransaction
        tx=VersionedTransaction.from_bytes(base64.b64decode(params[0]))
        assert tx.signatures==[Signature.default()]
        assert prep.to_bytes_versioned(tx.message)==messages[-1]
        simulations.append(tx)
        second=len(simulations)==2
        return {'context':{'slot':123},'value':{'unitsConsumed':200000 if second and failure=='second_units' else 100000,
            'err':{} if failure=='first_error' or second and failure=='second_error' else None,
            'replacementBlockhash':{} if failure=='replacement' else None}}
    monkeypatch.setattr(prep,'rpc',fake_rpc)
    if failure in ('replacement','fee_unknown'):
        with pytest.raises(ValueError):prep.two_phase_simulation(assembler,time.monotonic()+10)
        return
    tx,observation,fee,latest,measurement=prep.two_phase_simulation(assembler,time.monotonic()+10)
    assert limits==([1400000] if failure=='first_error' else [1400000,130000])
    assert measurement['passed'] is (failure is None)
    assert tx==simulations[-1]
    if failure!='first_error':assert messages[0]!=messages[1]


@pytest.mark.parametrize('failure',[None,'size','sell_quantity','debit','missing_balances',
    'fee_mismatch','native_conservation','existing_wsol','missing_token','identity_changed'])
def test_atomic_probe_retains_leg_order_and_is_never_separate_execution(monkeypatch,failure):
    from mykonos_meme_scout.jupiter_simulation import message_keys, token_states
    q,buy=canonical_build();wallet=q['taker']
    sell_query=dict(q,inputMint=prep.USDC,outputMint=prep.SOL,amount='99000')
    sell=copy.deepcopy(buy)
    sell.update(inputMint=prep.USDC,outputMint=prep.SOL,inAmount='99000',outAmount='1000000',otherAmountThreshold='990000')
    data=hashlib.sha256(b'global:route_v2').digest()[:8]+(99000).to_bytes(8,'little')+(1000000).to_bytes(8,'little')
    data+=(100).to_bytes(2,'little')+bytes(4)+(1).to_bytes(4,'little')+bytes([26,16,39,0,1])
    sell['swapInstruction']['data']=base64.b64encode(data).decode()
    a=sell['swapInstruction']['accounts'];a[1]['pubkey'],a[2]['pubkey']=a[2]['pubkey'],a[1]['pubkey']
    a[3]['pubkey'],a[4]['pubkey']=a[4]['pubkey'],a[3]['pubkey']
    sell['setupInstructions']=[sell['setupInstructions'][0],sell['setupInstructions'][3]]
    calls=[]
    def fake_worker(message,deadline):
        calls.append(message)
        if len(calls)==1:return buy
        assert message['query']==sell_query
        if failure=='sell_quantity':sell['inAmount']='99001'
        return sell
    monkeypatch.setattr(prep,'worker',fake_worker)
    monkeypatch.setattr(prep,'public_wallet',lambda:wallet)
    monkeypatch.setattr(prep,'rate_reservation',lambda *a,**kw:nullcontext())
    monkeypatch.setattr(prep,'load_tables',lambda *a:[])
    def fake_two_phase(assembler,deadline):
        if failure=='size':raise ValueError('transaction_too_large')
        tx=assembler(str(Hash.new_unique()),250000)
        # Exactly one limit/price; each leg's setup, swap, cleanup stays ordered.
        expected=prep.build_instructions(buy,q)[2:]+prep.build_instructions(sell,sell_query)[2:]
        assert len(tx.message.instructions)==len(expected)+2
        for compiled,original in zip(tx.message.instructions[2:],expected):
            assert bytes(compiled.data)==bytes(original.data)
            assert str(tx.message.account_keys[compiled.program_id_index])==str(original.program_id)
        keys,loaded=message_keys(tx,[])
        destination=keys.index(associated(wallet,prep.USDC,TOKEN));wsol=keys.index(associated(wallet,SOL,TOKEN))
        pre=[0]*len(keys);post=pre.copy();pre[0]=100000000;post[0]=97985000
        post[destination]=2000000
        pool=next(i for i in range(1,len(keys)) if i not in (destination,wsol))
        post[pool]=10000
        token={'accountIndex':destination,'mint':prep.USDC,'owner':wallet,'programId':TOKEN,
            'uiTokenAmount':{'amount':'1000','decimals':6}}
        before=[]
        if failure=='identity_changed':before=[dict(token,owner=str(Pubkey.new_unique()))]
        value={'err':None,'fee':5000,'unitsConsumed':100000,'loadedAddresses':loaded,
            'preBalances':pre,'postBalances':post,'preTokenBalances':before,'postTokenBalances':[token]}
        if failure=='debit':token['uiTokenAmount']['amount']='999';before=[dict(token,uiTokenAmount={'amount':'1000','decimals':6})];value['preTokenBalances']=before;pre[destination]=post[destination]=2000000;post[pool]+=2000000
        if failure=='missing_balances':value.pop('preBalances')
        if failure=='fee_mismatch':value['fee']=5001
        if failure=='native_conservation':post[0]+=1
        if failure=='existing_wsol':pre[wsol]=1;pre[0]-=1
        if failure=='missing_token':value['postTokenBalances']=[]
        observation={'context':{'slot':123},'value':value}
        return tx,observation,5000,{'blockhash':str(tx.message.recent_blockhash)},{'passed':True,'limit':250000,'initial_fee':50000}
    monkeypatch.setattr(prep,'two_phase_simulation',fake_two_phase)
    monkeypatch.setattr(prep,'rpc',lambda method,params,deadline:{'value':5000})
    if failure:
        with pytest.raises(ValueError):prep.prepare_roundtrip(1000000)
    else:
        report=prep.prepare_roundtrip(1000000)
        assert report['wallet_cash_delta_lamports']=='-2015000'
        assert report['remaining_middle_token_delta_raw']=='1000'
        assert report['net_bound_account_lamports']=='2000000'
        assert report['separate_message_fees_at_combined_budget']=={'buy':'5000','sell':'5000'}
        assert report['separate_exit_verified'] is False and report['execution_eligible'] is False
        assert report['chain_forward_startable'] is False
