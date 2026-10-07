"""Three isolated SPL/nonce VM cycles, NOT Jupiter/forward trades or performance.

Only Jupiter packet decoding is replaced for these native-instruction fixtures.
Token/cash changes, fees, HOLD and reconciliation execute in the actual local VM.
No network request, signature, imported token balance after BUY or forced exit.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import base64
import copy
import json

import pytest
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import transfer, TransferParams, withdraw_nonce_account, WithdrawNonceAccountParams
from solders.transaction import VersionedTransaction

from mykonos_meme_scout import live_parity_virtual as cycle
from mykonos_meme_scout.jupiter_stateful import StatefulJupiterBank, SYSTEM
from mykonos_meme_scout.jupiter_simulation import TOKEN, SOL, associated
from test_cabal_security import MINT, OWNER, DEPLOYER, payload, record, offline_network_barrier
from test_scoring import _base_snapshot


def raw(data=b'', owner=SYSTEM, lamports=1_000_000_000):
    return {'lamports': lamports, 'owner': owner, 'data': [base64.b64encode(data).decode(),'base64'],
            'executable': False, 'rentEpoch': 0}


def fixture(monkeypatch):
    wallet, nonce = Pubkey.new_unique(), Pubkey.new_unique()
    ata = associated(str(wallet), MINT, TOKEN)
    mint = bytearray(82); mint[:4] = (1).to_bytes(4,'little'); mint[4:36] = bytes(wallet)
    mint[44] = 6; mint[45] = 1
    token = bytearray(165); token[:32] = bytes(Pubkey.from_string(MINT)); token[32:64] = bytes(wallet); token[108] = 1
    nonce_data = (1).to_bytes(4,'little') + (1).to_bytes(4,'little') + bytes(wallet) + bytes(Hash.new_unique()) + (5000).to_bytes(8,'little')
    bank = StatefulJupiterBank(str(wallet), {str(wallet):raw(), str(nonce):raw(nonce_data),
        MINT:raw(bytes(mint),TOKEN), ata:raw(bytes(token),TOKEN,2_039_280)}, {}, 100)
    clock = [1000.2]
    runner = cycle.StatefulVirtualCycle(bank,MINT,clock=lambda:clock[0])
    # Explicit synthetic Jupiter envelope, NEVER a provider qualification.
    monkeypatch.setattr(cycle, 'validate_auxiliary', lambda *args: None)
    monkeypatch.setattr(cycle, 'assemble', lambda build,*args:build['fixture_tx'])
    def packet(side):
        token_ix = Instruction(Pubkey.from_string(TOKEN), bytes([7 if side=='BUY' else 8]) + (1000).to_bytes(8,'little'),
            [AccountMeta(Pubkey.from_string(MINT if side=='BUY' else ata),False,True),
             AccountMeta(Pubkey.from_string(ata if side=='BUY' else MINT),False,True), AccountMeta(wallet,True,False)])
        coin_ix = (transfer(TransferParams(from_pubkey=wallet,to_pubkey=nonce,lamports=100000)) if side=='BUY' else
                   withdraw_nonce_account(WithdrawNonceAccountParams(nonce_pubkey=nonce,authorized_pubkey=wallet,to_pubkey=wallet,lamports=98000)))
        blockhash = Hash.new_unique()
        msg = MessageV0.try_compile(wallet,[coin_ix,token_ix],[],blockhash)
        tx = VersionedTransaction.populate(msg,[Signature.default()])
        return {'query': {'taker':str(wallet),'inputMint':SOL if side=='BUY' else MINT,
            'outputMint':MINT if side=='BUY' else SOL,'amount':'100000' if side=='BUY' else '1000','slippageBps':100},
            'transaction':tx,'tables':[],'blockhash':str(blockhash),'quoted_at':clock[0],
            'source':'https://api.jup.ag/swap/v2/build',
            'build':{'otherAmountThreshold':'1000' if side=='BUY' else '98000','fixture_tx':tx,'priceImpactPct':'0'}}
    sec = {'checked':True,'checked_at':datetime.fromtimestamp(1000.,timezone.utc).isoformat(),
        'mint_authority_disabled':True,'freeze_authority_disabled':True,'honeypot':False,
        'top10_holder_pct':1.,'creator_holder_pct':0.,'metadata_mutable':False,
        'non_transferable':False,'transfer_hook_present':False,'malicious_creator':None}
    current = asdict(_base_snapshot(address=MINT,price_usd=.0011,liquidity_usd=150000.,market_cap_usd=300000.,
        volume_5m_usd=60000.,volume_1h_usd=300000.,buys_5m=100,sells_5m=20,price_change_5m_pct=30.,
        pair_created_at_ms=940000,captured_at=datetime.fromtimestamp(1000.,timezone.utc).isoformat(),
        profile_present=True,jupiter_price_impact_pct=.1))
    current['security'] = sec
    previous = copy.deepcopy(current); previous['price_usd'] = .001
    previous['captured_at'] = datetime.fromtimestamp(998.,timezone.utc).isoformat()
    previous['volume_5m_usd'] = 50000.
    holders = {'source':'SOLANA_RPC_FINALIZED_FULL_ACCOUNT_ENUMERATION','mint':MINT,
        'queried_owner':DEPLOYER,'concentration_complete':True,'supply_reconciliation':'PASS',
        'slot':100,'evidence_sha256':'0'*64,'requested_at':1000.,'received_at':1000.1,
        'top10_token_account_owner_pct':1.,'queried_owner_pct':0.}
    return runner,clock,packet,current,previous,holders


def buy(runner,packet,current,previous,holders):
    return runner.buy(packet('BUY'),current,previous,record(),OWNER,holders)


def test_three_independent_native_fixture_cycles_never_count_as_live_parity(monkeypatch,tmp_path):
    closed=[]
    for _ in range(3):
        runner,clock,packet,current,previous,holders=fixture(monkeypatch)
        assert runner._balance()==0
        buy(runner,packet,current,previous,holders)
        assert runner.hold()['token_raw']==1000
        assert runner.mark(packet('SELL'),current) is None
        # Existing MAX_HOLD, advanced fixture clock; no custom test exit.
        clock[0]+=runner.cfg['maximum_hold_seconds']
        current['captured_at']=datetime.fromtimestamp(clock[0],timezone.utc).isoformat()
        result=runner.sell(packet('SELL'),current)
        assert result['exit_reason']=='MAX_HOLD' and result['reconciliation']=='PASS'
        assert result['cash_delta_lamports']==-12000 and result['network_fees_lamports']==10000
        assert result['token_remaining_raw']==0 and result['modeled_net_lamports'] < -12000
        assert result['LIVE_PARITY_CLOSED'] is False
        closed.append(result)
        with pytest.raises(ValueError,match='duplicate_or_not_independent'):
            buy(runner,packet,current,previous,holders)
    assert len(closed)==3 and not any(r['LIVE_PARITY_CLOSED'] for r in closed)
    (tmp_path/'three-cycle-proof.json').write_text(json.dumps({
        'scope':'OFFLINE_SYNTHETIC_SPL_NONCE_FIXTURES_NOT_JUPITER_NOT_FORWARD',
        'native_fixture_closed':3,'LIVE_PARITY_CLOSED':0,'cycles':closed,
        'signing':False,'broadcast':False,'private_key_access':False,'real_money_moved':False}),encoding='utf-8')


def test_stale_quote_or_signature_cannot_mutate_bank(monkeypatch):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    before=runner.bank.state_hash(); p=packet('BUY'); p['quoted_at']-=3
    with pytest.raises(ValueError,match='stale'):
        runner.buy(p,current,previous,record(),OWNER,holders)
    assert runner.bank.state_hash()==before
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    before=runner.bank.state_hash(); p=packet('BUY')
    p['transaction']=VersionedTransaction.populate(p['transaction'].message,[Signature.from_bytes(bytes([1])*64)])
    with pytest.raises(ValueError,match='bytes_mismatch'):
        runner.buy(p,current,previous,record(),OWNER,holders)
    assert runner.bank.state_hash()==before


def test_missing_or_owner_holder_evidence_blocks_before_buy(monkeypatch):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    holders['queried_owner']=OWNER
    before=runner.bank.state_hash()
    with pytest.raises(ValueError,match='holder_binding'):
        buy(runner,packet,current,previous,holders)
    assert runner.bank.state_hash()==before


def test_no_exit_no_sell_and_no_hold_state_injection(monkeypatch):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    buy(runner,packet,current,previous,holders); before=runner.bank.state_hash()
    with pytest.raises(ValueError,match='exit_not_triggered'):
        runner.sell(packet('SELL'),current)
    assert runner.bank.state_hash()==before
    runner.position['token_raw']+=1
    with pytest.raises(ValueError,match='hold_not_preserved'):
        runner.hold()


def test_quote_expiring_during_native_preview_is_rejected_before_commit(monkeypatch):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    original=runner.bank.preview
    before=runner.bank.state_hash()
    def delayed(*args,**kwargs):
        result=original(*args,**kwargs);clock[0]+=3;return result
    monkeypatch.setattr(runner.bank,'preview',delayed)
    with pytest.raises(ValueError,match='stale'):
        buy(runner,packet,current,previous,holders)
    assert runner.bank.state_hash()==before and runner._balance()==0


@pytest.mark.parametrize('impact', [None, 'bad', 'NaN', 'Infinity', '1'])
def test_invalid_or_excessive_price_impact_stops_before_state_change(monkeypatch, impact):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    before=runner.bank.state_hash(); p=packet('BUY'); p['build']['priceImpactPct']=impact
    with pytest.raises(ValueError,match='virtual_guard_stop'):
        runner.buy(p,current,previous,record(),OWNER,holders)
    assert runner.stopped and runner.bank.state_hash()==before


def test_fee_cap_and_precommit_authority_change_block_mutation(monkeypatch):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    before=runner.bank.state_hash(); original=runner.bank.preview
    def expensive(*args,**kwargs):
        result=original(*args,**kwargs)
        result['cost']['network_fee_lamports']='3000001'
        return result
    monkeypatch.setattr(runner.bank,'preview',expensive)
    with pytest.raises(ValueError,match='fee_risk_limit'):
        buy(runner,packet,current,previous,holders)
    assert runner.bank.state_hash()==before and runner.stopped
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    before=runner.bank.state_hash(); original=runner.bank.preview
    previews=[0]
    def revocation(*args,**kwargs):
        result=original(*args,**kwargs); previews[0]+=1
        if previews[0]==2:
            monkeypatch.setattr(cycle,'authority_off',lambda: (_ for _ in ()).throw(ValueError('virtual_authority_rejected_stop_required')))
        return result
    monkeypatch.setattr(runner.bank,'preview',revocation)
    with pytest.raises(ValueError,match='authority_rejected'):
        buy(runner,packet,current,previous,holders)
    assert previews[0]==2 and runner.bank.state_hash()==before and runner.stopped


@pytest.mark.parametrize('expiring', ['holder', 'cabal', 'market', 'security'])
def test_buy_entry_evidence_expiring_during_preview_blocks_commit(monkeypatch,expiring):
    runner,clock,packet,current,previous,holders=fixture(monkeypatch)
    proof=payload()
    if expiring=='holder': holders.update(requested_at=995.3,received_at=995.4)
    if expiring=='cabal': proof['computed_at']=990.3
    if expiring=='market':
        current['captured_at']=datetime.fromtimestamp(990.3,timezone.utc).isoformat()
        previous['captured_at']=datetime.fromtimestamp(988.3,timezone.utc).isoformat()
    if expiring=='security': current['security']['checked_at']=datetime.fromtimestamp(990.3,timezone.utc).isoformat()
    original=runner.bank.preview; before=runner.bank.state_hash(); previews=[0]
    def delayed(*args,**kwargs):
        result=original(*args,**kwargs);previews[0]+=1
        if previews[0]==2: clock[0]+=.2
        return result
    monkeypatch.setattr(runner.bank,'preview',delayed)
    with pytest.raises(ValueError,match='entry_rejected|market_unknown_or_stale'):
        runner.buy(packet('BUY'),current,previous,record(proof),OWNER,holders)
    assert previews[0]==2 and runner.stopped and runner.bank.state_hash()==before
    assert runner._balance()==0
