"""Schema-2 migrations and lifecycle recovery on isolated ledgers only."""
import copy
import json
import sqlite3
from datetime import timedelta

import pytest
from test_execution import flow
from execution_state import Ledger,StateError,canonical
import execution_machine as machine

FIELDS={'execution_state','execution_side','execution_event_id','revision','unknown_reason',
        'terminal_unreconciled','exit_requested','rejection_kind','_original_doc_hash'}
FIELDS.add('reconciliation_after_generation')

@pytest.fixture(autouse=True)
def isolated_maintenance_owner_gate(monkeypatch):
    import execution_policy
    monkeypatch.setattr(execution_policy,'owner_gate',lambda:{'valid':True,'value':'OFF'})

def downgrade(store):
    with store.transaction() as c:
        for name in machine.WRITE_GUARDS:c.execute('DROP TRIGGER '+name)
        for row in list(c.execute('SELECT id,doc FROM trades')):
            doc=machine.read(row['doc'],StateError)
            legacy={k:v for k,v in doc.items() if k not in FIELDS}
            c.execute('UPDATE trades SET doc=? WHERE id=?',(canonical(legacy),row['id']))
        store.set_meta(c,'schema_version',1)

def raw_state(store):
    with sqlite3.connect(store.path) as c:
        return {t:list(c.execute('SELECT * FROM '+t+' ORDER BY rowid')) for t in ('meta','trades','events','receipts','audit')}

def test_complete_canonical_lifecycle_and_no_persisted_legacy_state(flow):
    buy=flow.buy();assert flow.trade(buy)['execution_state']=='READY'
    buy=flow.claim(buy);assert flow.trade(buy)['execution_state']=='BUY_REQUESTED'
    assert flow.store.feedback(flow.data(buy,'ACK'))
    assert flow.trade(buy)['execution_state']=='BUY_ACKNOWLEDGED'
    assert flow.store.feedback(*flow.fill(buy))
    assert flow.trade(buy)['execution_state']=='RECONCILING'
    assert flow.store.prepare_sell(buy['trade_id']) is None
    assert flow.reconcile();assert flow.trade(buy)['execution_state']=='POSITION_OPEN'
    sell=flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.trade(sell)['execution_state']=='SELL_REQUESTED'
    assert flow.store.feedback(flow.data(sell,'ACK'))
    assert flow.trade(sell)['execution_state']=='SELL_ACKNOWLEDGED'
    assert flow.store.feedback(*flow.fill(sell))
    assert flow.trade(sell)['execution_state']=='RECONCILING'
    assert flow.reconcile();assert flow.trade(sell)['execution_state']=='CLOSED'
    with flow.store.transaction() as c:
        stored=json.loads(c.execute('SELECT doc FROM trades').fetchone()[0])
        assert 'state' not in stored and '_original_doc_hash' not in stored
        phases=[json.loads(r[0])['to'] for r in c.execute("SELECT doc FROM audit WHERE action='execution_transition'")]
    assert phases==['RISK_CHECK','READY','BUY_REQUESTED','BUY_ACKNOWLEDGED','BUY_PARTIAL','BUY_FILLED',
                   'RECONCILING','POSITION_OPEN','SELL_REQUESTED','SELL_ACKNOWLEDGED','SELL_PARTIAL',
                   'SELL_FILLED','RECONCILING','CLOSED']

def test_partial_timeout_restart_terminalization_preserves_attempt_and_cash(flow):
    buy=flow.claim(flow.buy());data,proof=flow.fill(buy,status='PARTIAL')
    assert flow.store.feedback(data,proof)
    assert flow.trade(buy)['execution_state']=='BUY_PARTIAL'
    flow.now+=timedelta(seconds=31)
    assert flow.trade(buy)['execution_state']=='BUY_UNKNOWN'
    cash=flow.trade(buy)['wallet_delta_lamports']
    restarted=Ledger(flow.store.path,clock=flow.clock);restarted.recover()
    flow.store=restarted
    trade=flow.trade(buy)
    assert trade['execution_state']=='BUY_UNKNOWN' and trade['execution_event_id']==buy['event_id']
    assert trade['wallet_delta_lamports']==cash and restarted.pending() is None
    with pytest.raises(StateError,match='already_claimed'):flow.claim(buy)
    final=dict(data,status='FILLED',feedback_id='late-terminal-observation')
    assert flow.store.feedback(final,dict(proof,order_terminal=True))
    assert flow.trade(buy)['execution_state']=='RECONCILING'
    assert flow.trade(buy)['wallet_delta_lamports']==cash
    assert flow.reconcile();assert flow.trade(buy)['execution_state']=='POSITION_OPEN'

def test_canonical_partial_sell_is_not_reconciling_or_closed(flow):
    buy=flow.open();sell=flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell,status='PARTIAL',delta=-400,cash=20_000_000))
    assert flow.trade(sell)['execution_state']=='SELL_PARTIAL'
    flow.now+=timedelta(seconds=31);flow.store.recover()
    assert flow.trade(sell)['execution_state']=='SELL_UNKNOWN'
    assert flow.trade(sell)['remaining_raw']=='600'
    assert flow.store.prepare_sell(sell['trade_id']) is None

@pytest.mark.parametrize('source',['candidate','ready','requested','partial','position','closed'])
def test_schema1_migration_is_atomic_idempotent_and_evidence_based(flow,source):
    if source=='candidate':flow.candidate()
    elif source=='ready':flow.buy()
    elif source in {'requested','partial'}:
        buy=flow.claim(flow.buy())
        if source=='partial':assert flow.store.feedback(*flow.fill(buy,status='PARTIAL'))
    else:
        buy=flow.open()
        if source=='closed':
            sell=flow.claim(flow.store.prepare_sell(buy['trade_id']))
            assert flow.store.feedback(*flow.fill(sell));assert flow.reconcile()
    downgrade(flow.store);before=raw_state(flow.store)
    migrated=Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True);first=raw_state(migrated)
    assert migrated.status()['schema_version']==2
    expected={'candidate':'CANDIDATE','ready':'READY','requested':'BUY_REQUESTED',
              'partial':'BUY_PARTIAL','position':'RECONCILING','closed':'CLOSED'}
    assert migrated.status()['trades'][0]['execution_state']==expected[source]
    assert before['events']==first['events'] and before['receipts']==first['receipts']
    assert before['audit']==first['audit'][:-1]
    Ledger(flow.store.path,clock=flow.clock)
    assert raw_state(migrated)==first

def test_migration_rolls_back_all_rows_version_and_audit_on_late_write_failure(flow):
    first=flow.candidate();second=flow.candidate(signal_at=(flow.now-timedelta(seconds=1)).isoformat())
    downgrade(flow.store)
    with sqlite3.connect(flow.store.path) as c:
        c.execute("CREATE TRIGGER fail_second BEFORE UPDATE ON trades WHEN OLD.id='"+second+"' BEGIN SELECT RAISE(ABORT,'fixture migration failure'); END")
    before=raw_state(flow.store)
    with pytest.raises(sqlite3.IntegrityError):Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True)
    assert raw_state(flow.store)==before
    with sqlite3.connect(flow.store.path) as c:c.execute('DROP TRIGGER fail_second')
    assert Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True).status()['schema_version']==2

def test_migration_cannot_invent_terminal_fill_from_full_partial_quantities(flow):
    buy=flow.claim(flow.buy());assert flow.store.feedback(*flow.fill(buy,status='PARTIAL'))
    downgrade(flow.store)
    upgraded=Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True)
    assert upgraded.status()['trades'][0]['execution_state']=='BUY_PARTIAL'
    assert upgraded.status()['events'][0]['state']=='PARTIAL'
    assert upgraded.pending() is None

def test_migration_rejects_receipt_disagreement_without_changing_source(flow):
    buy=flow.open();downgrade(flow.store)
    with sqlite3.connect(flow.store.path) as c:
        c.row_factory=sqlite3.Row
        row=c.execute('SELECT signature,doc FROM receipts').fetchone();doc=json.loads(row['doc'])
        doc['proof']['token_delta_raw']='999'
        c.execute('UPDATE receipts SET doc=? WHERE signature=?',(canonical(doc),row['signature']))
    before=raw_state(flow.store)
    with pytest.raises(StateError,match='receipt_quantity_disagreement'):Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True)
    assert raw_state(flow.store)==before

def test_complete_document_cas_and_immutable_trade_identity(flow):
    tid=flow.candidate()
    with flow.store.transaction() as c:
        old=flow.store.get(c,'trades',tid);new=copy.deepcopy(old)
        new['shadow']={'source':'isolated-fixture'};flow.store.put(c,'trades',tid,new)
        old['shadow']={'source':'stale-fixture'}
        with pytest.raises(StateError,match='stale_trade_document'):flow.store.put(c,'trades',tid,old)
        corrupt=copy.deepcopy(new);corrupt['mint']='different'
        with pytest.raises(StateError,match='trade_identity_changed'):flow.store.put(c,'trades',tid,corrupt)
        corrupt=copy.deepcopy(new);corrupt['state']='APPROVED'
        with pytest.raises(StateError,match='legacy_projection_write_forbidden'):flow.store.put(c,'trades',tid,corrupt)

def test_cas_detects_out_of_band_document_change_even_with_same_revision(flow):
    tid=flow.candidate()
    with flow.store.transaction() as c:
        old=flow.store.get(c,'trades',tid)
        stored=json.loads(c.execute('SELECT doc FROM trades WHERE id=?',(tid,)).fetchone()[0])
        stored['shadow']={'source':'out-of-band-fixture'}
        c.execute('UPDATE trades SET doc=? WHERE id=?',(canonical(stored),tid))
        with pytest.raises(StateError,match='stale_trade_document'):flow.store.put(c,'trades',tid,old)

def test_illegal_transition_cannot_hide_unknown_order(flow):
    buy=flow.claim(flow.buy());before=raw_state(flow.store)
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',buy['trade_id'])
        with pytest.raises(StateError,match='illegal_execution_transition'):
            flow.store.machine_transition(c,trade,'RECONCILING','fixture bypass')
    assert raw_state(flow.store)==before

def test_pause_intent_survives_recoverable_kill_and_reconciliation(flow):
    buy=flow.claim(flow.buy());flow.store.control('pause_buys')
    flow.store.recover();assert flow.store.status()['control_state']=='KILLED'
    assert flow.store.feedback(*flow.fill(buy));assert flow.reconcile()
    assert flow.store.status()['control_state']=='PAUSED'
    assert flow.store.status()['new_buys_enabled'] is False

def test_initialized_writer_rejects_schema_downgrade_before_mutation(flow):
    flow.candidate();downgrade(flow.store);before=raw_state(flow.store)
    with pytest.raises(StateError,match='unknown_ledger_schema'):flow.store.control('pause_buys')
    assert raw_state(flow.store)==before

def test_direct_trade_put_cannot_skip_graph_or_replace_active_attempt(flow):
    tid=flow.candidate()
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',tid);invalid=copy.deepcopy(trade)
        invalid['execution_state']='READY';invalid['state']='APPROVED'
        with pytest.raises(StateError,match='illegal_execution_transition'):flow.store.put(c,'trades',tid,invalid)
    buy=flow.claim(flow.buy())
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',tid);invalid=copy.deepcopy(trade)
        invalid['execution_event_id']='different-attempt'
        with pytest.raises(StateError,match='execution_attempt_changed'):flow.store.put(c,'trades',tid,invalid)

def test_emergency_exit_before_reconciliation_is_only_durable_intent(flow):
    buy=flow.claim(flow.buy());assert flow.store.feedback(*flow.fill(buy))
    assert flow.store.control('emergency_exit')
    trade=flow.trade(buy)
    assert trade['execution_state']=='RECONCILING' and trade['exit_requested']
    assert not any(e['side']=='SELL' for e in flow.store.status()['events'])
    assert flow.store.prepare_sell(buy['trade_id']) is None
    assert flow.reconcile()
    sell=flow.store.pending()
    assert sell['side']=='SELL' and sell['exit_reason']=='emergency_exit'
    assert flow.trade(buy)['execution_state']=='POSITION_OPEN'

def test_initialized_schema1_writer_cannot_insert_after_migration(flow):
    tid=flow.candidate();downgrade(flow.store)
    # Keep the actual old SQL connection and upsert statement alive across the
    # migration, as a previously initialized schema-1 candidate writer would.
    writer=sqlite3.connect(flow.store.path,isolation_level=None)
    try:
        assert json.loads(writer.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])==1
        legacy=json.loads(writer.execute('SELECT doc FROM trades WHERE id=?',(tid,)).fetchone()[0])
        Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True);before=raw_state(flow.store)
        legacy['trade_id']='late-schema1-candidate'
        writer.execute('BEGIN IMMEDIATE')
        try:
            with pytest.raises(sqlite3.IntegrityError,match='SCHEMA2_TRADE_REQUIRED'):
                writer.execute('INSERT INTO trades VALUES(?,?) ON CONFLICT(id) DO UPDATE SET doc=excluded.doc',
                               (legacy['trade_id'],canonical(legacy)))
        finally:writer.rollback()
        assert raw_state(flow.store)==before
    finally:writer.close()

def test_migration_rejects_old_terminal_event_without_explicit_evidence(flow):
    buy=flow.open();downgrade(flow.store)
    with sqlite3.connect(flow.store.path) as c:
        row=c.execute('SELECT id,doc FROM events').fetchone();doc=json.loads(row[1])
        doc.pop('terminal_evidence')
        c.execute('UPDATE events SET doc=? WHERE id=?',(canonical(doc),row[0]))
    before=raw_state(flow.store)
    with pytest.raises(StateError,match='missing_persisted_terminal_evidence'):Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True)
    assert raw_state(flow.store)==before

def test_complete_partial_cannot_be_forged_terminal_by_changing_event_state(flow):
    buy=flow.claim(flow.buy());assert flow.store.feedback(*flow.fill(buy,status='PARTIAL'))
    with flow.store.transaction() as c:
        event=flow.store.get(c,'events',buy['event_id']);event['state']='FILLED'
        flow.store.put(c,'events',buy['event_id'],event)
        row=c.execute('SELECT doc FROM trades WHERE id=?',(buy['trade_id'],)).fetchone();doc=json.loads(row[0])
        doc.update(execution_state='RECONCILING',terminal_unreconciled=True)
        c.execute('UPDATE trades SET doc=? WHERE id=?',(canonical(doc),buy['trade_id']))
    with pytest.raises(StateError,match='missing_persisted_terminal_evidence'):flow.store.status()

def test_direct_put_valid_edge_is_audited_and_unreconciled_open_is_rejected(flow):
    tid=flow.candidate()
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',tid)
        trade.update(execution_state='RISK_CHECK',state='CANDIDATE')
        flow.store.put(c,'trades',tid,trade)
        assert c.execute("SELECT count(*) FROM audit WHERE action='execution_transition'").fetchone()[0]==1
    buy=flow.claim(flow.buy());assert flow.store.feedback(*flow.fill(buy))
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',tid)
        trade.update(execution_state='POSITION_OPEN',terminal_unreconciled=False,state='POSITION_OPEN')
        with pytest.raises(StateError,match='requires_reconciliation_evidence'):flow.store.put(c,'trades',tid,trade)

def test_missing_sql_write_guard_blocks_initialized_writer_before_changes(flow):
    flow.candidate()
    with flow.store.transaction() as c:c.execute('DROP TRIGGER imc_machine_insert_guard')
    before=raw_state(flow.store)
    with pytest.raises(StateError,match='invalid_machine_write_guard'):flow.store.control('pause_buys')
    assert raw_state(flow.store)==before

def test_reconciliation_round_trip_cannot_reuse_previous_generation(flow):
    buy=flow.open()
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',buy['trade_id']);prior=trade['position_reconciliation_revision']
        flow.store.machine_transition(c,trade,'RECONCILING','fresh_reconciliation_required')
        assert trade['position_reconciliation_revision'] is None
        assert flow.store.meta(c,'reconciliation_required') is True
        trade.update(execution_state='POSITION_OPEN',state='POSITION_OPEN',position_reconciliation_revision=prior)
        with pytest.raises(StateError,match='requires_reconciliation_evidence'):flow.store.put(c,'trades',buy['trade_id'],trade)
    assert flow.trade(buy)['execution_state']=='RECONCILING'
    assert flow.reconcile();assert flow.trade(buy)['execution_state']=='POSITION_OPEN'

@pytest.mark.parametrize('phase',['POSITION_OPEN','CLOSED'])
def test_central_writer_never_inserts_a_new_preopened_or_closed_trade(flow,phase):
    tid=flow.candidate()
    with flow.store.transaction() as c:
        doc=flow.store.get(c,'trades',tid);doc.pop('_original_doc_hash');doc['revision']=0
        doc['trade_id']='new-forged-trade';doc['execution_state']=phase;doc['state']=phase
        doc['buy_raw']='1000';doc['remaining_raw']='1000' if phase=='POSITION_OPEN' else '0'
        doc['sold_raw']='0' if phase=='POSITION_OPEN' else '1000'
        with pytest.raises(StateError,match='new_trade_requires_empty_candidate'):
            flow.store.put(c,'trades',doc['trade_id'],doc)

@pytest.mark.parametrize('binding',['missing','foreign'])
def test_buy_attempt_bind_must_reference_own_ready_event(flow,binding):
    buy=flow.buy()
    # A valid existing foreign event cannot authorize a separate candidate.
    other=flow.candidate(signal_at=(flow.now-timedelta(seconds=1)).isoformat())
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',other)
        flow.store.machine_transition(c,trade,'RISK_CHECK','fixture')
        flow.store.machine_transition(c,trade,'READY','fixture')
        trade.update(execution_side='BUY',execution_event_id='missing' if binding=='missing' else buy['event_id'])
        with pytest.raises(StateError,match='invalid_attempt_binding'):flow.store.put(c,'trades',other,trade)

def test_trigger_literal_change_is_detected_before_initialized_writer_mutates(flow):
    flow.candidate();name='imc_machine_insert_guard'
    with flow.store.transaction() as c:
        c.execute('DROP TRIGGER '+name)
        c.execute(machine.WRITE_GUARDS[name].replace("'$.state'","'$.s tate'"))
    before=raw_state(flow.store)
    with pytest.raises(StateError,match='invalid_machine_write_guard'):flow.store.control('pause_buys')
    assert raw_state(flow.store)==before

@pytest.mark.parametrize('value',[None,True,1.0,'1',-1])
@pytest.mark.parametrize('schema',[1,2])
def test_invalid_generation_metadata_is_never_repaired_or_accepted(flow,value,schema):
    flow.candidate()
    if schema==1:downgrade(flow.store)
    with sqlite3.connect(flow.store.path) as c:
        c.execute("UPDATE meta SET value=? WHERE key='reconciliation_generation'",(canonical(value),))
    before=raw_state(flow.store)
    with pytest.raises(StateError,match='corrupt_reconciliation_generation'):
        Ledger(flow.store.path,clock=flow.clock,migrate_schema1=schema==1)
    assert raw_state(flow.store)==before
    if schema==2:
        with pytest.raises(StateError,match='corrupt_reconciliation_generation'):flow.store.control('pause_buys')
        assert raw_state(flow.store)==before

@pytest.mark.parametrize('schema',[1,2])
def test_missing_generation_only_has_a_legacy_migration_default(flow,schema):
    flow.candidate()
    if schema==1:downgrade(flow.store)
    with sqlite3.connect(flow.store.path) as c:c.execute("DELETE FROM meta WHERE key='reconciliation_generation'")
    if schema==1:
        upgraded=Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True)
        with upgraded.transaction() as c:assert upgraded.meta(c,'reconciliation_generation')==0
    else:
        before=raw_state(flow.store)
        with pytest.raises(StateError,match='corrupt_reconciliation_generation'):Ledger(flow.store.path,clock=flow.clock)
        with pytest.raises(StateError,match='corrupt_reconciliation_generation'):flow.store.control('pause_buys')
        assert raw_state(flow.store)==before

def test_reconciliation_floor_blocks_old_audit_even_if_other_guards_are_forged_true(flow):
    buy=flow.open()
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',buy['trade_id']);prior=trade['position_reconciliation_revision']
        flow.store.machine_transition(c,trade,'RECONCILING','requires_new_proof')
        # Isolate the audit-generation floor from the simpler boolean guard.
        flow.store.set_meta(c,'reconciliation_required',False)
        trade.update(execution_state='POSITION_OPEN',state='POSITION_OPEN',position_reconciliation_revision=prior)
        with pytest.raises(StateError,match='position_reconciliation_evidence_mismatch'):
            flow.store.put(c,'trades',buy['trade_id'],trade)

def test_identical_snapshot_and_time_still_get_distinct_reconciliation_revisions(flow):
    buy=flow.open();before=flow.store.status()['reconciliation_revision']
    with flow.store.transaction() as c:generation=flow.store.meta(c,'reconciliation_generation')
    assert flow.reconcile()
    assert flow.store.status()['reconciliation_revision']!=before
    with flow.store.transaction() as c:assert flow.store.meta(c,'reconciliation_generation')==generation+1

@pytest.mark.parametrize('binding',['missing','foreign'])
def test_sell_attempt_bind_must_reference_own_sent_event(flow,binding):
    buy=flow.open()
    foreign='missing-sell'
    if binding=='foreign':
        flow.store.cfg['max_open_positions']=2
        second=flow.claim(flow.buy(mint='other-mint',signal_at=(flow.now-timedelta(seconds=1)).isoformat()))
        assert flow.store.feedback(*flow.fill(second,signature='x'*88));assert flow.reconcile()
        foreign=flow.claim(flow.store.prepare_sell(second['trade_id']))['event_id']
    with flow.store.transaction() as c:
        trade=flow.store.get(c,'trades',buy['trade_id'])
        trade.update(execution_state='SELL_REQUESTED',state='SELL_SENT',execution_side='SELL',execution_event_id=foreign)
        with pytest.raises(StateError,match='invalid_attempt_binding'):flow.store.put(c,'trades',buy['trade_id'],trade)

def test_normal_worker_start_cannot_automatically_migrate_schema1(flow):
    flow.candidate();downgrade(flow.store);before=raw_state(flow.store)
    with pytest.raises(StateError,match='requires_quiesced_writers'):Ledger(flow.store.path,clock=flow.clock)
    assert raw_state(flow.store)==before

def test_explicit_migration_cannot_run_with_invalid_or_on_owner_gate(flow,monkeypatch):
    import execution_policy
    flow.candidate();downgrade(flow.store);before=raw_state(flow.store)
    for gate in ({'valid':False,'value':'OFF'},{'valid':True,'value':'ON'}):
        monkeypatch.setattr(execution_policy,'owner_gate',lambda gate=gate:gate)
        with pytest.raises(StateError,match='migration_requires_owner_gate_off'):
            Ledger(flow.store.path,clock=flow.clock,migrate_schema1=True)
        assert raw_state(flow.store)==before

@pytest.mark.parametrize('value',[None,0,1,'true',[],{}])
def test_migration_opt_in_requires_exact_bool_before_filesystem_access(tmp_path,value):
    path=tmp_path/'unopened.db'
    with pytest.raises(StateError,match='invalid_migration_request'):Ledger(path,migrate_schema1=value)
    assert not path.exists() and not path.with_suffix('.db.init.lock').exists()
