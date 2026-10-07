"""Both directions of the closed deployed Raydium layout; mutations deny."""
import base64
import hashlib
import math
import sys
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

sys.path.insert(0,str(Path(__file__).parents[1]/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_jupiter_shadow import IsolatedBank,ix
from mykonos_meme_scout.jupiter_route import verify,RAYDIUM
from mykonos_meme_scout.jupiter_prepare import SOL,USDC,JUPITER
from mykonos_meme_scout.jupiter_simulation import TOKEN,associated


def fixture(buy,legacy_observation=False):
    bank=IsolatedBank();bank.tokens=1000000
    bank.token_account(associated(bank.wallet,USDC,TOKEN),USDC,bank.wallet,bank.tokens)
    query={'taker':bank.wallet,'inputMint':SOL if buy else USDC,'outputMint':USDC if buy else SOL,
           'amount':'10000000' if buy else '1000000','slippageBps':100,'dexes':'Raydium CLMM'}
    build=bank.build(query)
    pda=lambda seeds:str(Pubkey.find_program_address(seeds,Pubkey.from_string(RAYDIUM))[0])
    pk=lambda k:bytes(Pubkey.from_string(k))
    a,b=sorted((SOL,USDC),key=pk);config=pda([b'amm_config',b'\x00\x01'])
    pool=pda([b'pool',pk(config),pk(a),pk(b)])
    va,vb=(pda([b'pool_vault',pk(pool),pk(m)]) for m in (a,b))
    obs=str(Pubkey.new_unique()) if legacy_observation else pda([b'observation',pk(pool)])
    bitmap=pda([b'pool_tick_array_bitmap_extension',pk(pool)])
    body=bytearray(1544);body[:8]=hashlib.sha256(b'account:PoolState').digest()[:8]
    for offset,key in ((9,config),(73,a),(105,b),(137,va),(169,vb),(201,obs)):body[offset:offset+32]=pk(key)
    body[235:237]=(64).to_bytes(2,'little');body[237:253]=(10**18).to_bytes(16,'little')
    body[253:269]=int(math.sqrt(.12 if a==SOL else 1/.12)*2**64).to_bytes(16,'little')
    body[904:1032]=sum(1<<i for i in range(510,515)).to_bytes(128,'little')
    bank.put(pool,RAYDIUM,body)
    cfg=bytearray(117);cfg[:8]=hashlib.sha256(b'account:AmmConfig').digest()[:8]
    cfg[9:11]=b'\x01\x00';cfg[47:51]=(200).to_bytes(4,'little');cfg[51:53]=(64).to_bytes(2,'little')
    bank.put(config,RAYDIUM,cfg)
    observation=bytearray(4483);observation[:8]=hashlib.sha256(b'account:ObservationState').digest()[:8]
    observation[19:51]=pk(pool);bank.put(obs,RAYDIUM,observation)
    extension=bytearray(1832);extension[:8]=hashlib.sha256(b'account:TickArrayBitmapExtension').digest()[:8]
    extension[8:40]=pk(pool);bank.put(bitmap,RAYDIUM,extension)
    bank.token_account(va,a,pool,10**14 if a==SOL else 10**12)
    bank.token_account(vb,b,pool,10**14 if b==SOL else 10**12)
    a_to_b=query['inputMint']==a;ticks=[]
    for index in (0,-1,-2) if a_to_b else (0,1,2):
        start=index*3840;key=pda([b'tick_array',pk(pool),start.to_bytes(4,'big',signed=True)])
        data=bytearray(10240);data[:8]=hashlib.sha256(b'account:TickArrayState').digest()[:8]
        data[8:40]=pk(pool);data[40:44]=start.to_bytes(4,'little',signed=True)
        bank.put(key,RAYDIUM,data);ticks.append(key)
    source=associated(bank.wallet,query['inputMint'],TOKEN);dest=associated(bank.wallet,query['outputMint'],TOKEN)
    dex=[RAYDIUM,bank.wallet,config,pool,source,dest,va if a_to_b else vb,vb if a_to_b else va,obs,TOKEN,
         ticks[0],bitmap,ticks[1],ticks[2],JUPITER]
    header=build['swapInstruction']['accounts'][:10]
    data=base64.b64decode(build['swapInstruction']['data'])[:30]+b'\x01\x00\x00\x00\x1a\x10\x27\x00\x01'
    build['swapInstruction']=ix(JUPITER,data,[(x['pubkey'],x['isSigner'],x['isWritable']) for x in header]
        +[(key,False,3<=i<=8 or 10<=i<=13) for i,key in enumerate(dex)])
    build['routePlan'][0]['swapInfo'].update(ammKey=pool,label='Raydium CLMM')
    return bank,query,build,{'pool':pool,'config':config,'bitmap':bitmap,'ticks':ticks,'vault':va,'observation':obs}


@pytest.mark.parametrize('buy',[True,False])
@pytest.mark.parametrize('legacy',[True,False])
def test_raydium_closed_route_both_directions(buy,legacy):
    bank,query,build,keys=fixture(buy,legacy)
    evidence=verify(build,query,bank.accounts.get)
    assert evidence['full_route_semantics_verified']
    assert evidence['pool_fee_rate_millionths']==200 and float(evidence['liquidity_usd'])>100000


@pytest.mark.parametrize('change',['enum','trailing','privilege','wallet','mint','vault','pool_owner','config_fee',
    'spacing','dynamic','seed','status','bitmap','tick_start','tick_order','observation','observation_address',
    'observation_owner','observation_length','observation_discriminator','liquidity','quote_dex'])
def test_raydium_unknown_or_mismatched_evidence_fails_closed(change):
    bank,query,build,keys=fixture(True)
    def mutate(key,offset,data):
        account=bank.accounts[key];body=bytearray(base64.b64decode(account['data'][0]));body[offset:offset+len(data)]=data
        account['data'][0]=base64.b64encode(body).decode()
    accounts=build['swapInstruction']['accounts']
    if change in ('enum','trailing'):
        data=bytearray(base64.b64decode(build['swapInstruction']['data']))
        if change=='enum':data[34]=27
        else:data+=b'\x00'
        build['swapInstruction']['data']=base64.b64encode(data).decode()
    elif change=='privilege':accounts[12]['isWritable']=True
    elif change=='wallet':accounts[11]['pubkey']=str(Pubkey.new_unique())
    elif change=='mint':mutate(keys['pool'],73,bytes(Pubkey.new_unique()))
    elif change=='vault':mutate(keys['vault'],32,bytes(Pubkey.new_unique()))
    elif change=='pool_owner':bank.accounts[keys['pool']]['owner']=JUPITER
    elif change=='config_fee':mutate(keys['config'],47,(10001).to_bytes(4,'little'))
    elif change=='spacing':mutate(keys['config'],51,b'\x01\x00')
    elif change=='dynamic':mutate(keys['pool'],1096,b'\x01')
    elif change=='seed':mutate(keys['pool'],391,b'\x01')
    elif change=='status':mutate(keys['pool'],389,b'\x10')
    elif change=='bitmap':mutate(keys['bitmap'],8,bytes(Pubkey.new_unique()))
    elif change=='tick_start':mutate(keys['ticks'][0],40,(3840).to_bytes(4,'little',signed=True))
    elif change=='tick_order':accounts[22],accounts[23]=accounts[23],accounts[22]
    elif change=='observation':mutate(keys['observation'],19,bytes(Pubkey.new_unique()))
    elif change=='observation_address':accounts[18]['pubkey']=str(Pubkey.new_unique())
    elif change=='observation_owner':bank.accounts[keys['observation']]['owner']=JUPITER
    elif change=='observation_length':bank.accounts[keys['observation']]['data'][0]=base64.b64encode(bytes(4482)).decode()
    elif change=='observation_discriminator':mutate(keys['observation'],0,bytes(8))
    elif change=='liquidity':mutate(keys['pool'],237,bytes(16))
    elif change=='quote_dex':query['dexes']='Whirlpool'
    with pytest.raises((ValueError,KeyError)):verify(build,query,bank.accounts.get)
