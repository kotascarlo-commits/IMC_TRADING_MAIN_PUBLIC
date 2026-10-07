"""Reviewed, atomic nonfinancial wallet initialization; no real keys/RPC."""
import sys
from pathlib import Path
import copy
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_state import Ledger,timestamp,StateError
from execution_validation import migrate_unexecuted_wallet,require
from execution_jupiter_shadow import IsolatedBank
import mykonos_meme_scout.jupiter_read as read


def fixture(tmp_path,monkeypatch):
    bank=IsolatedBank();store=Ledger(tmp_path/'execution.db');store.bootstrap(timestamp())
    assert store.reconcile({'wallet':'legacy-wallet','observed_at':timestamp(),'chain_positions':{},
                            'executor_positions':{},'orders':{},'balance_lamports':'500000000'})
    monkeypatch.setattr(read,'public_wallet',lambda:bank.wallet)
    snapshot=dict(bank.snapshot(timestamp),source='LOCAL_PUBLIC_WALLET_AND_FINALIZED_RPC',commitment='finalized',chain_slot=bank.slot)
    return store,bank,snapshot


def test_atomic_wallet_baseline_contract_and_inventory_no_generation_reset(tmp_path,monkeypatch):
    from solders.pubkey import Pubkey
    store,bank,snapshot=fixture(tmp_path,monkeypatch);foreign={str(Pubkey.new_unique()):'12345'}
    snapshot['chain_positions']=foreign
    with store.transaction() as c:before=store.meta(c,'reconciliation_generation')
    assert migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    with store.transaction() as c:
        assert store.meta(c,'wallet')==bank.wallet
        assert store.meta(c,'initial_balance_lamports')==str(bank.balance)
        assert store.meta(c,'reconciliation_generation')==before
        assert store.meta(c,'reconciliation_required')
        assert not store.meta(c,'new_buys_enabled')
        assert require(store,c)['domain']=='FINALIZED_CHAIN'
        record=store.meta(c,'validation_wallet_migration')
        assert record['before']['wallet']=='legacy-wallet'
        assert record['foreign_inventory']==foreign
    assert not migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)


@pytest.mark.parametrize('change',['stale','source','commitment','slot','wallet','balance','pair_inventory','missing_guard'])
def test_migration_rejects_unsafe_source_without_partial_contract(tmp_path,monkeypatch,change):
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    guard=lambda:None
    if change=='stale':snapshot['observed_at']='2020-01-01T00:00:00+00:00'
    if change=='source':snapshot['source']='caller'
    if change=='commitment':snapshot['commitment']='confirmed'
    if change=='slot':snapshot['chain_slot']=False
    if change=='wallet':snapshot['wallet']='wrong'
    if change=='balance':snapshot['balance_lamports']='0'
    if change=='pair_inventory':
        from mykonos_meme_scout.jupiter_prepare import USDC
        snapshot['chain_positions']={USDC:'100'}
    if change=='missing_guard':
        def guard():raise StateError('maintenance_missing')
    with pytest.raises(StateError):migrate_unexecuted_wallet(store,snapshot,maintenance_guard=guard)
    with store.transaction() as c:
        assert store.meta(c,'wallet')=='legacy-wallet' and require(store,c) is None
        assert store.meta(c,'validation_wallet_migration') is None


def test_commit_guard_loss_rolls_back_every_baseline_field(tmp_path,monkeypatch):
    store,bank,snapshot=fixture(tmp_path,monkeypatch);calls=[]
    def guard():
        calls.append(1)
        if len(calls)>1:raise StateError('maintenance_lost')
    with pytest.raises(StateError):migrate_unexecuted_wallet(store,snapshot,maintenance_guard=guard)
    with store.transaction() as c:
        assert store.meta(c,'wallet')=='legacy-wallet' and require(store,c) is None
        assert store.meta(c,'validation_foreign_inventory') is None


def test_startup_reads_existing_migration_without_writer_or_file_change(tmp_path,monkeypatch):
    from execution_validation import startup_state
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    assert migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    before=store.path.read_bytes()
    monkeypatch.setattr(Ledger,'__init__',lambda *a,**k:pytest.fail('startup must not initialize a writer'))
    state=startup_state(store.path)
    assert state['mode']=='THREE_TRADE_VALIDATION' and state['wallet']==bank.wallet
    assert not state['broadcast'] and not state['signing']
    assert store.path.read_bytes()==before


@pytest.mark.parametrize('change',['missing_migration','migration_identity','wallet','contract_identity','domain'])
def test_startup_never_promotes_missing_or_corrupt_wallet_evidence(tmp_path,monkeypatch,change):
    from execution_validation import startup_state
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    with store.transaction() as c:
        if change=='missing_migration':store.set_meta(c,'validation_wallet_migration',None)
        elif change=='wallet':store.set_meta(c,'wallet','wrong-wallet')
        elif change=='migration_identity':
            value=store.meta(c,'validation_wallet_migration');value['contract_identity']='wrong'
            store.set_meta(c,'validation_wallet_migration',value)
        else:
            value=store.meta(c,'validation_contract')
            value['identity' if change=='contract_identity' else 'domain']='wrong'
            store.set_meta(c,'validation_contract',value)
    with pytest.raises(StateError):startup_state(store.path)


@pytest.mark.parametrize('enabled',[False,True])
def test_future_schema_two_status_keeps_real_gate_values_in_fixture_only(tmp_path,monkeypatch,enabled):
    from execution_validation import startup_state
    import execution_state
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    cfg=dict(store.cfg,schema_version=2,execution_provider='JUPITER_V2',execution_mode='THREE_TRADE_VALIDATION',
             execution_authority_pct=100 if enabled else 0)
    cfg.update({k:enabled for k in ('signing','broadcast_enabled','live_trading_enabled','wallet_access')})
    monkeypatch.setattr(execution_state,'load_config',lambda:cfg)
    state=startup_state(store.path)
    assert state['broadcast'] is enabled and state['signing'] is enabled and state['live_trading'] is enabled


def test_only_classified_temporary_read_before_start_can_reconcile_without_abort(tmp_path,monkeypatch):
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    for _ in range(store.cfg['max_api_failures']):store.api_failure('rpc_minimum_slot',temporary=True)
    with store.transaction() as c:
        assert store.meta(c,'validation_abort') is None
        assert store.meta(c,'kill_reason')=='api_failure_limit'
    assert store.reconcile(dict(snapshot,executor_positions={},orders={}))
    with store.transaction() as c:assert store.meta(c,'kill_reason') is None


def test_started_validation_temporary_read_aborts_on_first_failure(tmp_path,monkeypatch):
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    with store.transaction() as c:store.set_meta(c,'validation_started',True)
    store.api_failure('rpc_minimum_slot',temporary=True)
    with store.transaction() as c:assert store.meta(c,'validation_abort')=='validation_read_failure'


@pytest.mark.parametrize('unsafe',[None,'started','other_reason','balance','foreign','guard'])
def test_prestart_read_recovery_is_exact_and_never_clears_other_failures(tmp_path,monkeypatch,unsafe):
    from execution_validation import recover_prestart_read_abort
    store,bank,snapshot=fixture(tmp_path,monkeypatch)
    migrate_unexecuted_wallet(store,snapshot,maintenance_guard=lambda:None)
    store.api_failure('StateError')
    if unsafe=='started':
        with store.transaction() as c:store.set_meta(c,'validation_started',True)
    if unsafe=='other_reason':store.block('route_mismatch')
    if unsafe=='balance':snapshot['balance_lamports']='12345678'
    if unsafe=='foreign':snapshot['chain_positions']={'wrong':'1'}
    def guard():
        if unsafe=='guard':raise StateError('guard_lost')
    if unsafe:
        with pytest.raises(StateError):recover_prestart_read_abort(store,snapshot,maintenance_guard=guard)
        with store.transaction() as c:assert store.meta(c,'validation_abort')=='validation_read_failure'
    else:
        assert recover_prestart_read_abort(store,snapshot,maintenance_guard=guard)
        with store.transaction() as c:
            assert store.meta(c,'validation_abort') is None and store.meta(c,'validation_prestart_recovery_pending')
            assert store.meta(c,'reconciliation_required') and not store.meta(c,'new_buys_enabled')
