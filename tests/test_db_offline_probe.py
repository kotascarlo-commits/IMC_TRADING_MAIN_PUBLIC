"""Real native CLI on isolated fixtures; never production database/processes."""
import json
import os
import sqlite3
import subprocess
import sys
import uuid
import importlib.util
import copy
from pathlib import Path

import pytest
from mykonos_meme_scout.store import SCHEMA

ROOT = Path(__file__).parents[1]
CLI = ROOT / '.local-tools/sqlite-tools-3530400/sqlite3.exe'
pytestmark = pytest.mark.skipif(os.name != 'nt' or not CLI.exists(), reason='Exact local Windows SQLite audit binary')


def run(tmp_path, *, cli=CLI, malformed=False, seconds=10,allocation_mode='global',auto_vacuum=0,extra_object=None):
    database = tmp_path / 'source.db'
    with sqlite3.connect(database) as c:
        c.execute('PRAGMA auto_vacuum='+str(auto_vacuum))
        c.executescript(SCHEMA)
        if extra_object is not None:c.execute('CREATE TABLE "'+extra_object.replace('"','""')+'"(value TEXT)')
        for i in range(1, 6):
            payload = '{bad' if malformed else json.dumps({'captured_at': str(i), 'price_usd': 1})
            c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)', (i, str(i), 'mint', 'TEST', 1, payload))
    c.close()  # sqlite3's transaction context does not close its writer handle
    # Leave real committed WAL data while proving its fixture writer has exited.
    code = ("import os,sqlite3,sys;c=sqlite3.connect(sys.argv[1]);"
            "c.execute(\"INSERT INTO schema_meta VALUES('wal_fixture','visible')\");c.commit();os._exit(0)")
    assert subprocess.run([sys.executable, '-c', code, str(database)], timeout=5).returncode == 0
    wal = Path(str(database)+'-wal')
    assert wal.stat().st_size > 0
    init, output = tmp_path/'empty.sql', tmp_path/'report.json'
    init.write_bytes(b'')
    nonce = uuid.uuid4().hex
    before = database.read_bytes()
    wal_before = wal.read_bytes()
    completed = subprocess.run([sys.executable, str(ROOT/'scripts/db_offline_probe.py'),
        '--database', str(database), '--cli', str(cli), '--init', str(init), '--output', str(output),
        '--nonce', nonce, '--seconds', str(seconds),'--allocation-mode',allocation_mode], capture_output=True, text=True, timeout=15)
    assert completed.stdout == '' and completed.stderr == ''
    report = json.loads(output.read_text())
    assert report['nonce'] == nonce and report['phase'] == 'FINAL' and report['cleanup_confirmed'] is True
    assert database.read_bytes() == before
    assert wal.read_bytes() == wal_before
    with database.open('r+b'): pass
    return completed, report


def test_complete_probe_has_real_allocation_and_explicit_sample_provenance(tmp_path):
    completed, report = run(tmp_path)
    assert completed.returncode == 0 and report['complete'] and report['metadata_unchanged']
    assert report['fatal_error'] is None and report['wal_included'] is True
    queries = {q['name']: q for q in report['queries']}
    sample = queries['sample_snapshots']['results'][-1][0]
    assert sample['sampled_rows'] == 5 and sample['min_id'] == 1 and sample['max_id'] == 5
    assert sample['distinct_comparisons'] == 1
    metadata = queries['metadata']['results'][-2][0]
    allocation = queries['allocation']['results'][-1]
    assert sum(o['allocated_pages'] for o in allocation)+metadata['freelist_count'] == metadata['page_count']
    assert sum(o['allocated_bytes'] for o in allocation) == metadata['page_size']*metadata['page_count']
    assert all(q['complete'] and q['child_exit_confirmed'] for q in queries.values())
    assert len({q['shared_deadline'] for q in queries.values()}) == 1


def test_bad_json_preserves_metadata_but_does_not_invent_complete_sample(tmp_path):
    completed, report = run(tmp_path, malformed=True)
    assert completed.returncode == 2 and not report['complete']
    queries = {q['name']: q for q in report['queries']}
    assert queries['metadata']['complete']
    assert not queries['sample_snapshots']['complete'] and queries['sample_snapshots']['returncode'] != 0
    assert queries['allocation']['complete']


def test_wrong_binary_hash_never_spawns_cli_or_claims_measurements(tmp_path):
    fake = tmp_path/'sqlite3.exe'
    fake.write_bytes(b'not the official executable')
    completed, report = run(tmp_path, cli=fake)
    assert completed.returncode == 2 and report['fatal_error'] == 'ValueError'
    assert report['queries'] == [] and not report['complete']


def contract(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location('offline_probe_test', ROOT/'scripts/db_offline_probe.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fault', ['none','nonce','phase','duplicate_name','sql_error','io_error',
    'output_limit','fatal_empty','files_changed','complete_mismatch','exit_mismatch','native_exit','sample_shape','bool_prefix'])
def test_final_contract_rejects_invalid_reports_and_accepts_clean_budget(tmp_path, monkeypatch, fault):
    completed, report = run(tmp_path)
    module = contract(monkeypatch)
    source = Path(report['source'])
    nonce, exit_code, current = report['nonce'], completed.returncode, module.state(source)
    assert module.validate_final(report,source=source,nonce=nonce,exit_code=exit_code,current_files=current)['complete']
    altered = copy.deepcopy(report)
    if fault == 'none':
        altered['queries'][-1].update(complete=False,timed_out=True,returncode=1,results=[])
        altered['page_accounting']=module.metadata_accounting(altered['queries'][0]['results'][-2][0],0)
        altered['complete']=False; exit_code=2
        assert not module.validate_final(altered,source=source,nonce=nonce,exit_code=exit_code,current_files=current)['complete']
        return
    if fault == 'nonce': altered['nonce']='0'*32
    if fault == 'phase': altered['phase']='RUNNING'
    if fault == 'duplicate_name': altered['queries'][-1]['name']='metadata'
    if fault == 'sql_error': altered['queries'][-1].update(returncode=1,complete=False);altered['complete']=False;exit_code=2
    if fault == 'io_error': altered['queries'][-1]['io_error']=True
    if fault == 'output_limit': altered['queries'][-1]['output_limit_exceeded']=True
    if fault == 'fatal_empty': altered['fatal_error']=''
    if fault == 'files_changed': altered['files_after']['main']['bytes']+=1
    if fault == 'complete_mismatch': altered['complete']=False
    if fault == 'exit_mismatch': exit_code=2
    if fault == 'native_exit': altered['queries'][-1]['child_exit_confirmed']=False
    if fault == 'sample_shape': altered['queries'][2]['results'][-1][0]['sampled_rows']=True
    if fault == 'bool_prefix': altered['queries'][2]['results'][1]=[{'mmap_size':False}]
    with pytest.raises(ValueError): module.validate_final(altered,source=source,nonce=nonce,exit_code=exit_code,current_files=current)


@pytest.mark.parametrize('body', ['{"nonce":"first","nonce":"second"}', '{"value":NaN}'])
def test_report_parser_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path, monkeypatch, body):
    path = tmp_path/'unsafe.json'; path.write_text(body)
    with pytest.raises(ValueError): contract(monkeypatch).read_final(path)


@pytest.mark.parametrize('seconds',[True,False,float('nan'),float('inf'),1,901,'900'])
def test_invalid_probe_budget_rejected_before_file_or_child_access(tmp_path,monkeypatch,seconds):
    module=contract(monkeypatch)
    def forbidden(*args,**kwargs):raise AssertionError('invalid budget touched a native guard')
    monkeypatch.setattr(module.native,'ReadFiles',forbidden)
    with pytest.raises(ValueError,match='invalid_offline_probe_parameters'):
        module.probe(tmp_path/'missing.db',tmp_path/'missing.exe',tmp_path/'missing.sql',tmp_path/'out.json','a'*32,seconds=seconds)
    assert not (tmp_path/'out.json').exists()


def test_extended_budget_is_verified_against_actual_request(tmp_path,monkeypatch):
    completed,report=run(tmp_path,seconds=900)
    module=contract(monkeypatch)
    args=dict(source=Path(report['source']),nonce=report['nonce'],exit_code=completed.returncode,current_files=module.state(Path(report['source'])))
    assert report['budget_seconds']==900 and module.validate_final(report,expected_seconds=900,**args)['complete']
    with pytest.raises(ValueError):module.validate_final(report,expected_seconds=180,**args)
    for invalid in (True,float('nan'),float('inf'),1,901,'900'):
        changed=copy.deepcopy(report);changed['budget_seconds']=invalid
        with pytest.raises(ValueError):module.validate_final(changed,**args)


@pytest.mark.parametrize('pages,maps,locks',[(1048576,6765,0),(1048577,6765,1),(1048578,6766,1)])
def test_pointer_map_lock_collision_keeps_original_raster(monkeypatch,pages,maps,locks):
    # 1024-byte pages, 250 reserved bytes: stride 155. Its candidate at
    # lock page 1048577 moves to 1048578; following candidates stay put.
    module=contract(monkeypatch)
    value=module.page_accounting(pages,1024,250,1,0,pages-maps-locks)
    assert value['pointer_map_pages']==maps and value['lock_pages']==locks
    assert value['unmeasured_btree_pages']==0
    assert value['logical_bytes']==pages*1024


@pytest.mark.parametrize('pages,locks',[(262144,0),(262145,1),(262146,1)])
def test_lock_page_boundary_without_large_fixture(monkeypatch,pages,locks):
    value=contract(monkeypatch).page_accounting(pages,4096,0,0,0,pages-locks)
    assert value['lock_pages']==locks and value['pointer_map_pages']==0
    assert value['unmeasured_btree_pages']==0


@pytest.mark.parametrize('values',[(True,4096,0,0,0,0),(1,4096,256,0,0,0),
    (1,512,33,0,0,0),(1,513,0,0,0,0),(1,4096,0,3,0,0),
    (1,4096,0,0,2,0),(1,4096,0,0,0,2)])
def test_invalid_page_accounting_never_becomes_a_size(monkeypatch,values):
    with pytest.raises(ValueError):contract(monkeypatch).page_accounting(*values)


@pytest.mark.parametrize('auto_vacuum',[0,1,2])
def test_native_object_measurements_and_logical_header_balance_all_modes(tmp_path,monkeypatch,auto_vacuum):
    completed,report=run(tmp_path,allocation_mode='objects',auto_vacuum=auto_vacuum)
    module=contract(monkeypatch)
    assert completed.returncode==0 and report['complete']
    metadata=report['queries'][0]['results'][-2][0]
    assert metadata['auto_vacuum']==auto_vacuum and metadata['reserved_hex']=='00'
    assert metadata['header_magic']=='53514C69746520666F726D6174203300'
    assert metadata['header_page_bytes']==metadata['page_size']
    accounting=report['page_accounting']
    assert accounting['pointer_map_pages']==(1 if auto_vacuum else 0)
    assert accounting['unmeasured_btree_pages']==0
    assert accounting['measured_btree_pages']+accounting['pointer_map_pages']+accounting['lock_pages']+accounting['freelist_pages']==metadata['page_count']
    assert module.validate_final(report,source=Path(report['source']),nonce=report['nonce'],exit_code=0,
        current_files=module.state(Path(report['source'])),expected_allocation_mode='objects')['complete']


@pytest.mark.parametrize('fault',['missing','duplicate','inventory','wrong_name','bool_pages','page_bytes',
    'negative','partial_bad_member','accounting','reserved_missing','reserved_bad','magic','header_size','mode'])
def test_object_contract_rejects_bad_evidence_even_in_partial_report(tmp_path,monkeypatch,fault):
    completed,report=run(tmp_path,allocation_mode='objects')
    module=contract(monkeypatch)
    changed=copy.deepcopy(report);members=changed['allocation_objects']
    row=members[0]['results'][-1][0];meta=changed['queries'][0]['results'][-2][0]
    if fault=='missing':members.pop()
    elif fault=='duplicate':members.append(copy.deepcopy(members[0]))
    elif fault=='inventory':changed['allocation_inventory'].append('invented')
    elif fault=='wrong_name':row['name']='invented'
    elif fault=='bool_pages':row['allocated_pages']=True
    elif fault=='page_bytes':row['allocated_bytes']+=1
    elif fault=='negative':row['payload_bytes']=-1
    elif fault=='partial_bad_member':
        changed['complete']=False;row['allocated_pages']=True
        members[-1].update(complete=False,timed_out=True,returncode=1,results=[])
    elif fault=='accounting':changed['page_accounting']['unmeasured_btree_pages']=1
    elif fault=='reserved_missing':meta.pop('reserved_hex')
    elif fault=='reserved_bad':meta['reserved_hex']='GG'
    elif fault=='magic':meta['header_magic']='0'*32
    elif fault=='header_size':meta['header_page_bytes']+=1
    elif fault=='mode':changed['allocation_mode']='global'
    with pytest.raises((ValueError,KeyError)):
        module.validate_final(changed,source=Path(report['source']),nonce=report['nonce'],
            exit_code=2 if not changed['complete'] else 0,current_files=module.state(Path(report['source'])),
            expected_allocation_mode='objects')


def test_completed_members_are_persisted_before_real_later_timeout(tmp_path,monkeypatch):
    run(tmp_path) # create an isolated genuine WAL source
    module=contract(monkeypatch)
    original_sql=module.allocation_sql;original_save=module.save;seen=[]
    def sql(name):
        if name=='snapshots':return 'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n'
        return original_sql(name)
    def save(report,path):
        original_save(report,path)
        # Read the actual atomically published file, not the mutable object.
        seen.append(json.loads(path.read_text()))
    monkeypatch.setattr(module,'allocation_sql',sql);monkeypatch.setattr(module,'save',save)
    database=tmp_path/'source.db';before=database.read_bytes();wal=Path(str(database)+'-wal');wal_before=wal.read_bytes()
    output=tmp_path/'members.json'
    report=module.probe(database,CLI,tmp_path/'empty.sql',output,'b'*32,seconds=8,allocation_mode='objects')
    assert not report['complete'] and report['cleanup_confirmed'] and report['fatal_error'] is None
    assert any(item['phase']=='RUNNING' and item['allocation_objects'] and item['allocation_objects'][0]['complete'] for item in seen)
    assert report['allocation_objects'][-1]['timed_out']
    assert all(item['complete'] for item in report['allocation_objects'][:-1])
    assert report['page_accounting']['unmeasured_btree_pages']>0
    assert module.validate_final(report,source=database.resolve(),nonce='b'*32,exit_code=2,
        current_files=module.state(database),expected_allocation_mode='objects')['complete'] is False
    assert database.read_bytes()==before and wal.read_bytes()==wal_before


def test_object_name_literal_escaping_and_report_byte_limit(tmp_path,monkeypatch):
    module=contract(monkeypatch)
    assert module.allocation_sql("odd'name").endswith("WHERE name='odd''name'")
    path=tmp_path/'too-large.json'
    with pytest.raises(ValueError,match='offline_report_oversize'):module.save({'nonce':'a'*32,'padding':'x'*131072},path)
    assert not path.exists() and not list(tmp_path.glob('*.tmp'))


def test_real_quoted_object_name_cannot_inject_sql_into_measurement(tmp_path,monkeypatch):
    name="odd';DROP TABLE snapshots;--"
    completed,report=run(tmp_path,allocation_mode='objects',extra_object=name)
    assert completed.returncode==0 and report['complete']
    found=next(q for q in report['allocation_objects'] if q['name']=='allocation_object:'+name)
    assert found['results'][-1][0]['name']==name and found['complete']
    module=contract(monkeypatch)
    assert module.validate_final(report,source=Path(report['source']),nonce=report['nonce'],exit_code=0,
        current_files=module.state(Path(report['source'])),expected_allocation_mode='objects')['complete']
    with sqlite3.connect(Path(report['source']).as_uri()+'?mode=ro',uri=True) as c:
        assert c.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==5
