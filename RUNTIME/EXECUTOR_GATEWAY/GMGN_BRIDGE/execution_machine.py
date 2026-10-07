"""Schema-2 execution lifecycle. Legacy state is a read-only projection.

Order attempts remain events in the same transaction and database. Pause and
kill are independent persistent system guards, not replacements for an order.
"""
import hashlib
import json

GRAPH = {
    'CANDIDATE': {'RISK_CHECK','REJECTED'},
    'RISK_CHECK': {'READY','REJECTED'},
    'READY': {'BUY_REQUESTED','REJECTED'},
    'BUY_REQUESTED': {'BUY_ACKNOWLEDGED','BUY_PARTIAL','BUY_UNKNOWN','BUY_FILLED'},
    'BUY_ACKNOWLEDGED': {'BUY_PARTIAL','BUY_UNKNOWN','BUY_FILLED'},
    'BUY_PARTIAL': {'BUY_UNKNOWN','BUY_FILLED'},
    'BUY_UNKNOWN': {'BUY_ACKNOWLEDGED','BUY_PARTIAL','BUY_FILLED'},
    'BUY_FILLED': {'RECONCILING'},
    'POSITION_OPEN': {'SELL_REQUESTED','RECONCILING'},
    'SELL_REQUESTED': {'SELL_ACKNOWLEDGED','SELL_PARTIAL','SELL_UNKNOWN','SELL_FILLED'},
    'SELL_ACKNOWLEDGED': {'SELL_PARTIAL','SELL_UNKNOWN','SELL_FILLED'},
    'SELL_PARTIAL': {'SELL_UNKNOWN','SELL_FILLED'},
    'SELL_UNKNOWN': {'SELL_ACKNOWLEDGED','SELL_PARTIAL','SELL_FILLED'},
    'SELL_FILLED': {'RECONCILING'},
    'RECONCILING': {'POSITION_OPEN','CLOSED'},
    'CLOSED': set(), 'REJECTED': set(),
}
SYSTEM_STATES = {'PAUSED','KILLED'}
WRITE_GUARDS = {name: f"""CREATE TRIGGER {name} BEFORE {operation} ON trades
WHEN json_type(NEW.doc,'$.execution_state') IS NOT 'text'
  OR json_type(NEW.doc,'$.state') IS NOT NULL
  OR json_type(NEW.doc,'$.revision') IS NOT 'integer'
  OR json_extract(NEW.doc,'$.revision') < 1
BEGIN SELECT RAISE(ABORT,'SCHEMA2_TRADE_REQUIRED'); END"""
    for name,operation in (('imc_machine_insert_guard','INSERT'),('imc_machine_update_guard','UPDATE'))}
UNRESOLVED = {s for s in GRAPH if s.endswith(('_REQUESTED','_ACKNOWLEDGED','_PARTIAL','_UNKNOWN'))}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def projection(doc):
    state=doc['execution_state']
    if state in {'CANDIDATE','RISK_CHECK'}:return 'CANDIDATE'
    if state=='READY':return 'APPROVED'
    if state.endswith('_REQUESTED'):return doc['execution_side']+'_SENT'
    if state.endswith('_ACKNOWLEDGED'):return doc['execution_side']+'_ACK'
    if state.endswith('_PARTIAL'):return 'RECONCILIATION_REQUIRED'
    if state.endswith('_UNKNOWN'):
        if doc['unknown_reason']=='RECOVERY':return 'RECONCILIATION_REQUIRED'
        return 'TIMEOUT' if doc['unknown_reason']=='TIMEOUT' else 'UNKNOWN_EXECUTION'
    if state=='RECONCILING':
        if doc['terminal_unreconciled']:
            return 'POSITION_OPEN' if doc['execution_side']=='BUY' else 'SELL_FILLED'
        return 'RECONCILIATION_REQUIRED'
    if state=='POSITION_OPEN' and doc['exit_requested']:return 'EMERGENCY_EXIT'
    if state=='REJECTED' and doc['rejection_kind']=='FAILED':return 'FAILED'
    return state


def validate(doc,error=ValueError):
    if not isinstance(doc,dict):raise error('corrupt_ledger_document')
    required={'execution_state','execution_side','execution_event_id','revision','unknown_reason',
              'terminal_unreconciled','exit_requested','rejection_kind'}
    required.add('reconciliation_after_generation')
    if not required.issubset(doc):raise error('corrupt_trade_schema')
    state=doc['execution_state'];side=doc['execution_side']
    if not isinstance(state,str) or state not in GRAPH or side not in (None,'BUY','SELL'):
        raise error('corrupt_execution_state')
    if type(doc['revision']) is not int or doc['revision'] < 0:
        raise error('corrupt_trade_revision')
    if type(doc['reconciliation_after_generation']) is not int or doc['reconciliation_after_generation']<0:
        raise error('corrupt_reconciliation_generation')
    if any(type(doc[key]) is not bool for key in ('terminal_unreconciled','exit_requested')):
        raise error('corrupt_execution_context')
    if not isinstance(doc['unknown_reason'],str) or doc['rejection_kind'] not in (None,'FAILED'):
        raise error('corrupt_execution_context')
    if state.startswith(('BUY_','SELL_')) and side!=state.split('_')[0]:
        raise error('execution_side_mismatch')
    if state in UNRESOLVED | {'BUY_FILLED','SELL_FILLED','RECONCILING'} and (
            side is None or not isinstance(doc['execution_event_id'],str) or not doc['execution_event_id']):
        raise error('execution_attempt_missing')
    if doc['terminal_unreconciled'] and state!='RECONCILING':
        raise error('invalid_terminal_reconciliation_context')
    if 'state' in doc and doc['state']!=projection(doc):
        raise error('legacy_projection_write_forbidden')
    return doc


def seed(doc,*,state='CANDIDATE',side=None,event_id=None):
    doc.update(execution_state=state,execution_side=side,execution_event_id=event_id,revision=0,
               unknown_reason='',terminal_unreconciled=False,exit_requested=False,rejection_kind=None,
               reconciliation_after_generation=0)
    doc['state']=projection(doc)
    return doc


def move(doc,target,*,error=ValueError,reason='',terminal_unreconciled=False):
    origin=doc['execution_state']
    if target not in GRAPH or (target!=origin and target not in GRAPH[origin]):
        raise error('illegal_execution_transition:'+origin+':'+str(target))
    doc['execution_state']=target
    doc['terminal_unreconciled']=terminal_unreconciled
    if target.endswith('_UNKNOWN'):doc['unknown_reason']=reason
    elif target not in UNRESOLVED:doc['unknown_reason']=''
    doc['state']=projection(doc)
    return origin


def storage(doc,error=ValueError):
    validate(doc,error)
    return {k:v for k,v in doc.items() if k not in {'state','_original_doc_hash','_transition_reason'}}


def read(text,error=ValueError):
    doc=json.loads(text)
    if isinstance(doc,dict) and 'state' in doc:raise error('persisted_legacy_state_forbidden')
    validate(doc,error)
    if doc['revision']==0:raise error('corrupt_trade_revision')
    doc['state']=projection(doc)
    doc['_original_doc_hash']=digest(text)
    return doc
