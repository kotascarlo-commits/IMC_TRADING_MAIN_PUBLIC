"""Read-only, bounded native SQLite measurements; never stops/starts IMC.

The owning controller must exclude/recheck writers and confirm its own Job
shutdown before releasing its guards. This worker report alone is insufficient.
"""
import argparse
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import windows_read_guard as native

CLI_SHA256 = '5da2398d4913b893bd1ea578d85403b3a83a06fabf9d2303ca9f63ef0849fc6f'


def page_accounting(page_count, page_size, reserved_bytes, auto_vacuum, freelist_count, measured_pages):
    """SQLite logical-page accounting; unmeasured pages are never table sizes."""
    values=(page_count,page_size,reserved_bytes,auto_vacuum,freelist_count,measured_pages)
    if any(type(v) is not int for v in values):raise ValueError('invalid_page_accounting')
    if (not 1 <= page_count <= 4294967294 or page_size < 512 or page_size > 65536
            or page_size & (page_size-1) or not 0 <= reserved_bytes <= 255
            or page_size-reserved_bytes < 480 or auto_vacuum not in (0,1,2)
            or freelist_count < 0 or measured_pages < 0):raise ValueError('invalid_page_accounting')
    lock_page=1073741824//page_size+1
    locks=int(page_count>=lock_page)
    maps=0
    if auto_vacuum and page_count>=2:
        stride=(page_size-reserved_bytes)//5+1
        maps=(page_count-2)//stride+1
        # Only this candidate is displaced, not the subsequent raster.
        if page_count==lock_page and (lock_page-2)%stride==0:maps-=1
    unmeasured=page_count-freelist_count-maps-locks-measured_pages
    if unmeasured<0:raise ValueError('invalid_page_accounting')
    return {'logical_pages':page_count,'logical_bytes':page_count*page_size,
        'measured_btree_pages':measured_pages,'measured_btree_bytes':measured_pages*page_size,
        'freelist_pages':freelist_count,'freelist_bytes':freelist_count*page_size,
        'pointer_map_pages':maps,'pointer_map_bytes':maps*page_size,
        'lock_pages':locks,'lock_bytes':locks*page_size,
        'unmeasured_btree_pages':unmeasured,'unmeasured_btree_bytes':unmeasured*page_size}


def state(database):
    result = {}
    for name, path in [('main', database), ('wal', Path(str(database)+'-wal'))]:
        try:
            s = path.stat()
            result[name] = {'device': s.st_dev, 'file_id': s.st_ino, 'bytes': s.st_size,
                            'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns}
        except FileNotFoundError:
            if name == 'main': raise
            result[name] = None
    return result


def save(report, path):
    temporary = path.with_name(path.name+'.'+report['nonce']+'.tmp')
    data = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    if len(data)>131072:raise ValueError('offline_report_oversize')
    with temporary.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def queries():
    yield 'metadata', 5, (
        "SELECT sqlite_version() AS sqlite_version, "
        "(SELECT page_size FROM pragma_page_size) AS page_size, "
        "(SELECT page_count FROM pragma_page_count) AS page_count, "
        "(SELECT freelist_count FROM pragma_freelist_count) AS freelist_count, "
        "(SELECT auto_vacuum FROM pragma_auto_vacuum) AS auto_vacuum, "
        "hex(substr(data,1,16)) AS header_magic, length(data) AS header_page_bytes, "
        "hex(substr(data,21,1)) AS reserved_hex FROM sqlite_dbpage('main') WHERE pgno=1; "
        "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_schema ORDER BY type,name")
    yield 'id_boundaries', 5, (
        "SELECT 'snapshots' AS name,(SELECT id FROM snapshots ORDER BY id LIMIT 1) AS min_id,"
        "(SELECT id FROM snapshots ORDER BY id DESC LIMIT 1) AS max_id UNION ALL "
        "SELECT 'decisions',(SELECT id FROM decisions ORDER BY id LIMIT 1),(SELECT id FROM decisions ORDER BY id DESC LIMIT 1) UNION ALL "
        "SELECT 'learning_predictions',(SELECT id FROM learning_predictions ORDER BY id LIMIT 1),"
        "(SELECT id FROM learning_predictions ORDER BY id DESC LIMIT 1)")
    # This is a latest-ID sample, not random sampling or a global savings proof.
    for table, expression in [('snapshots', "json_remove(payload_json,'$.captured_at')"),
                              ('decisions', "reasons_json||char(0)||components_json"),
                              ('learning_predictions', 'reasons_json')]:
        raw = 'payload_json' if table == 'snapshots' else expression
        yield 'sample_'+table, 8, (
            f'WITH sample AS (SELECT id,{raw} AS raw,{expression} AS comparison FROM {table} '
            'ORDER BY id DESC LIMIT 10000), unique_values AS (SELECT DISTINCT comparison FROM sample) '
            'SELECT COUNT(1) AS sampled_rows,MIN(id) AS min_id,MAX(id) AS max_id, '
            'SUM(length(CAST(raw AS BLOB))) AS raw_text_bytes, '
            '(SELECT COUNT(1) FROM unique_values) AS distinct_comparisons, '
            '(SELECT SUM(length(CAST(comparison AS BLOB))) FROM unique_values) AS distinct_comparison_bytes FROM sample')
    yield 'allocation', 865, (
        "SELECT name,pageno AS allocated_pages,pgsize AS allocated_bytes,"
        "payload AS payload_bytes,unused AS unused_bytes FROM dbstat('main',1)")


def allocation_inventory(schema):
    names={'sqlite_schema'}
    for row in schema:
        if type(row['rootpage']) is not int or type(row['name']) is not str:raise ValueError('allocation_inventory_invalid')
        if row['rootpage']>0:names.add(row['name'])
    if any(not name or '\0' in name or len(name)>1024 for name in names):raise ValueError('allocation_inventory_invalid')
    # An order heuristic, not measured size. Keep the three long record
    # tables last so their timeout cannot erase completed earlier evidence.
    large={'snapshots','decisions','learning_predictions'}
    return sorted(names,key=lambda name:(name in large,name))


def allocation_sql(name):
    if type(name) is not str or not name or '\0' in name:raise ValueError('allocation_name_invalid')
    literal="'"+name.replace("'","''")+"'"
    return ("SELECT name,pageno AS allocated_pages,pgsize AS allocated_bytes,"
        "payload AS payload_bytes,unused AS unused_bytes FROM dbstat('main',1) WHERE name="+literal)


def metadata_accounting(meta,measured_pages):
    if (meta['header_magic']!='53514C69746520666F726D6174203300'
            or type(meta['header_page_bytes']) is not int or meta['header_page_bytes']!=meta['page_size']
            or type(meta['reserved_hex']) is not str or not re.fullmatch('[0-9A-F]{2}',meta['reserved_hex'])):
        raise ValueError('logical_header_invalid')
    return page_accounting(meta['page_count'],meta['page_size'],int(meta['reserved_hex'],16),
        meta['auto_vacuum'],meta['freelist_count'],measured_pages)


def object_result(rows,name,page_size):
    if type(rows) is not list or len(rows)!=1:raise ValueError('allocation_object_invalid')
    row=rows[0]
    if (type(row) is not dict or set(row)!={'name','allocated_pages','allocated_bytes','payload_bytes','unused_bytes'}
            or row['name']!=name or any(type(v)is not int or v<0 for k,v in row.items() if k!='name')
            or row['allocated_pages']<1 or row['allocated_bytes']!=row['allocated_pages']*page_size
            or row['payload_bytes']+row['unused_bytes']>row['allocated_bytes']):raise ValueError('allocation_object_invalid')
    return row


def probe(database, cli, init_file, output, nonce, *, seconds=180, allocation_mode='global'):
    if not re.fullmatch('[a-f0-9]{32}', nonce) or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 2 <= seconds <= 900:
        raise ValueError('invalid_offline_probe_parameters')
    if allocation_mode not in ('global','objects'):raise ValueError('invalid_allocation_mode')
    database, cli, init_file = (Path(p).resolve(strict=True) for p in (database, cli, init_file))
    output = Path(output).resolve()
    protected = [database, cli, init_file]
    wal = Path(str(database)+'-wal')
    if wal.exists(): protected.append(wal.resolve(strict=True))
    if output in protected or output == Path(str(database)+'-shm') or not output.parent.is_dir():
        raise ValueError('invalid_offline_probe_output')
    started = time.monotonic()
    report = {'schema_version': 2, 'nonce': nonce, 'phase': 'RUNNING', 'complete': False,
              'cleanup_confirmed': False, 'source': str(database), 'pid': os.getpid(),
              'started_utc': datetime.now(timezone.utc).isoformat(), 'queries': [], 'fatal_error': None,
              'opened_mode': 'ro', 'wal_included': True, 'budget_seconds': seconds,
              'allocation_mode':allocation_mode,'allocation_inventory':[],'allocation_objects':[],
              'page_accounting':None,
              'snapshot_scope': 'separate fixed BEGIN/schema-read snapshot per query group',
              'source_evidence': 'native file identities/sharing plus outside-SQL metadata; no content hash',
              'allocation_scope': 'allocated pages/bytes, not reclaimable bytes',
              'sample_scope': 'latest <=10000 IDs per named table; normalized comparison only, not lossless compression proof',
              'shm_scope': 'may change SQLite bookkeeping; excluded from main/WAL metadata comparison'}
    held = None
    try:
        with native.ReadFiles(protected, deadline=started+seconds) as held:
            report['file_guards'] = held.evidence
            report['files_before'] = state(database)
            digest = hashlib.sha256()
            with cli.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1048576), b''): digest.update(chunk)
            if digest.hexdigest() != CLI_SHA256: raise ValueError('offline_probe_cli_hash_mismatch')
            report['cli_sha256'] = digest.hexdigest()
            save(report, output)
            for label, limit, sql in queries():
                if label=='allocation' and allocation_mode=='objects':continue
                remaining = held.deadline-time.monotonic()-.25
                if remaining <= 2:
                    report['queries'].append({'name': label, 'complete': False, 'state': 'NOT_RUN_BUDGET', 'results': None})
                else:
                    measured = native.readonly_cli(cli, database, sql, init_file, guards=held,
                                                   seconds=min(limit, remaining))
                    report['queries'].append({'name': label, 'state': 'MEASURED', **measured})
                save(report, output)  # preserve completed metadata before expensive scans
            first=report['queries'][0]
            if first['complete']:
                meta=first['results'][-2][0];schema=first['results'][-1]
                inventory=allocation_inventory(schema);report['allocation_inventory']=inventory
                metadata_accounting(meta,0) # no unverified R/default-header fallback
                pages=0
                if allocation_mode=='objects':
                    for name in inventory:
                        label='allocation_object:'+name
                        remaining=held.deadline-time.monotonic()-.25
                        if remaining<=2:
                            value={'name':label,'complete':False,'state':'NOT_RUN_BUDGET','results':None}
                        else:
                            value={'name':label,'state':'MEASURED',**native.readonly_cli(cli,database,
                                allocation_sql(name),init_file,guards=held,seconds=min(120,remaining))}
                            if value['complete']:pages+=object_result(value['results'][-1],name,meta['page_size'])['allocated_pages']
                        report['allocation_objects'].append(value)
                        report['page_accounting']=metadata_accounting(meta,pages)
                        save(report,output)
                elif report['queries'][-1]['complete']:
                    objects=report['queries'][-1]['results'][-1]
                    for name in inventory:
                        pages+=object_result([row for row in objects if row['name']==name],name,meta['page_size'])['allocated_pages']
                report['page_accounting']=metadata_accounting(meta,pages)
            report['files_after'] = state(database)
            report['metadata_unchanged'] = report['files_before'] == report['files_after']
            report['complete'] = report['metadata_unchanged'] and all(q['complete'] for q in report['queries'])
            if allocation_mode=='objects':report['complete']=report['complete'] and all(q['complete'] for q in report['allocation_objects'])
            if report['complete'] and report['page_accounting']['unmeasured_btree_pages']!=0:raise ValueError('allocation_page_mismatch')
    except Exception as exc:
        report['fatal_error'] = type(exc).__name__  # no raw provider/SQL/exception strings
        report['complete'] = False
    report['cleanup_confirmed'] = held is not None and not held.handles and not native._RETAINED_GUARDS
    report['phase'] = 'FINAL'
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    save(report, output)
    return report


def validate_final(report, *, source, nonce, exit_code, current_files, expected_seconds=None, expected_allocation_mode=None):
    """Strict maintenance completion contract; clean budgets are not SQL errors."""
    def require(value):
        if not value: raise ValueError('offline_final_contract_invalid')
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    def files(value):
        require(type(value) is dict and set(value) == {'main', 'wal'})
        for name, entry in value.items():
            if name == 'wal' and entry is None: continue
            require(type(entry) is dict and set(entry) == {'device','file_id','bytes','mtime_ns','ctime_ns'})
            require(all(type(v) is int and v >= 0 for v in entry.values()))
    require(type(report) is dict and type(exit_code) is int and exit_code in (0, 2))
    require(number(report['budget_seconds']) and 2 <= report['budget_seconds'] <= 900)
    if expected_seconds is not None:
        require(number(expected_seconds) and 2 <= expected_seconds <= 900
                and report['budget_seconds'] == expected_seconds)
    require(type(report['schema_version']) is int and report['schema_version'] == 2)
    mode=report['allocation_mode'];require(mode in ('global','objects'))
    if expected_allocation_mode is not None:require(mode==expected_allocation_mode)
    require(report['nonce'] == nonce and report['phase'] == 'FINAL' and report['source'] == str(source))
    require(type(report['complete']) is bool and report['cleanup_confirmed'] is True and report['fatal_error'] is None)
    require(report['opened_mode'] == 'ro' and report['wal_included'] is True and report['cli_sha256'] == CLI_SHA256)
    require(type(report['pid']) is int and report['pid'] > 0 and number(report['elapsed_seconds']))
    for value in (report['files_before'], report['files_after'], current_files): files(value)
    require(report['metadata_unchanged'] is True and report['files_before'] == report['files_after'] == current_files)
    require(type(report['queries']) is list and len(report['queries']) == (6 if mode=='global' else 5))
    expected = tuple(name for name, _, _ in queries() if mode=='global' or name!='allocation')
    require(tuple(q['name'] for q in report['queries']) == expected)
    deadlines, completed = set(), []
    require(type(report['allocation_objects']) is list)
    for query in report['queries']+report['allocation_objects']:
        require(type(query) is dict and type(query['complete']) is bool)
        if query['state'] == 'NOT_RUN_BUDGET':
            require(set(query) == {'name','complete','state','results'} and not query['complete'] and query['results'] is None)
            completed.append(False); continue
        require(query['state'] == 'MEASURED' and set(query) == {'name','state','complete','returncode','timed_out',
            'output_limit_exceeded','io_error','captured_bytes','shared_deadline','child_exit_confirmed',
            'elapsed_seconds','results','stderr_present'})
        require(query['child_exit_confirmed'] is True and query['io_error'] is False and query['output_limit_exceeded'] is False)
        require(query['stderr_present'] is False and type(query['timed_out']) is bool and type(query['returncode']) is int)
        require(type(query['captured_bytes']) is int and 0 <= query['captured_bytes'] <= 1048576)
        require(number(query['shared_deadline']) and number(query['elapsed_seconds']))
        deadlines.add(query['shared_deadline'])
        require(query['complete'] == (query['returncode'] == 0 and not query['timed_out']))
        require(query['complete'] or query['timed_out'])  # no arbitrary SQL/API/IO errors
        result = query['results']
        require(type(result) is list and all(type(rows) is list and all(type(row) is dict for row in rows) for rows in result))
        prefix = [[{'timeout': 1000}], [{'mmap_size': 0}]]
        for i in range(min(2, len(result))):
            require(result[i] == prefix[i] and len(result[i]) == 1
                    and all(type(v) is int for v in result[i][0].values()))
        if len(result) >= 3:
            require(len(result[2]) == 1 and set(result[2][0]) == {'schema_objects'})
            require(type(result[2][0]['schema_objects']) is int and result[2][0]['schema_objects'] >= 0)
        require(len(result) <= (5 if query['name'] == 'metadata' else 4))
        if query['complete']: require(len(result) == (5 if query['name'] == 'metadata' else 4))
        if query['complete'] and query['name'] == 'id_boundaries':
            rows = result[-1]
            require(len(rows) == 3 and {r['name'] for r in rows} == {'snapshots','decisions','learning_predictions'})
            for row in rows:
                require(set(row) == {'name','min_id','max_id'})
                require((row['min_id'] is None and row['max_id'] is None) or
                        (type(row['min_id']) is int and type(row['max_id']) is int and 0 < row['min_id'] <= row['max_id']))
        if query['complete'] and query['name'].startswith('sample_'):
            require(len(result[-1]) == 1)
            row = result[-1][0]
            require(set(row) == {'sampled_rows','min_id','max_id','raw_text_bytes','distinct_comparisons','distinct_comparison_bytes'})
            require(type(row['sampled_rows']) is int and 0 <= row['sampled_rows'] <= 10000
                    and type(row['distinct_comparisons']) is int and 0 <= row['distinct_comparisons'] <= row['sampled_rows'])
            if row['sampled_rows'] == 0:
                require(row['distinct_comparisons'] == 0 and all(row[k] is None for k in ('min_id','max_id','raw_text_bytes','distinct_comparison_bytes')))
            else:
                require(all(type(row[k]) is int and row[k] >= 0 for k in ('min_id','max_id','raw_text_bytes','distinct_comparison_bytes'))
                        and 0 < row['min_id'] <= row['max_id'] and row['distinct_comparisons'] > 0)
        completed.append(query['complete'])
    require(len(deadlines) == 1 and report['queries'][0]['complete'])
    metadata, schema = report['queries'][0]['results'][-2:]
    require(len(metadata) == 1 and set(metadata[0]) == {'sqlite_version','page_size','page_count','freelist_count',
        'auto_vacuum','header_magic','header_page_bytes','reserved_hex'})
    meta = metadata[0]
    require(meta['sqlite_version'] == '3.53.4' and all(type(meta[k]) is int and meta[k] >= 0 for k in ('page_size','page_count','freelist_count')))
    require(meta['page_size'] > 0 and meta['page_count'] >= meta['freelist_count'])
    require(all(set(row) == {'type','name','tbl_name','rootpage','sql'} and type(row['rootpage']) is int
                and row['rootpage'] >= 0 and row['type'] in ('table','index','view','trigger')
                and all(type(row[k]) is str for k in ('name','tbl_name'))
                and (row['sql'] is None or type(row['sql']) is str) for row in schema))
    require(len({row['name'] for row in schema}) == len(schema))
    inventory=allocation_inventory(schema)
    require(report['allocation_inventory']==inventory)
    measured=[]
    if mode=='objects':
        require([q['name'] for q in report['allocation_objects']]==['allocation_object:'+name for name in inventory])
        for name,query in zip(inventory,report['allocation_objects']):
            if query['complete']:measured.append(object_result(query['results'][-1],name,meta['page_size']))
    else:
        require(not report['allocation_objects'])
    allocation = report['queries'][-1]
    if mode=='global' and allocation['complete']:
        objects = allocation['results'][-1]
        require(all(set(o) == {'name','allocated_pages','allocated_bytes','payload_bytes','unused_bytes'}
                    and type(o['name']) is str and all(type(v) is int and v >= 0 for k,v in o.items() if k != 'name') for o in objects))
        require({o['name'] for o in objects} == {'sqlite_schema'} | {row['name'] for row in schema if row['rootpage'] > 0})
        require(len({o['name'] for o in objects}) == len(objects))
        measured=[object_result([row for row in objects if row['name']==name],name,meta['page_size']) for name in inventory]
        require(all(o['allocated_pages']*meta['page_size'] == o['allocated_bytes']
                    and o['payload_bytes']+o['unused_bytes'] <= o['allocated_bytes'] for o in objects))
    accounting=metadata_accounting(meta,sum(row['allocated_pages'] for row in measured))
    require(json.dumps(report['page_accounting'],sort_keys=True,allow_nan=False)==json.dumps(accounting,sort_keys=True,allow_nan=False))
    complete = all(completed)
    if complete:require(accounting['unmeasured_btree_pages']==0)
    require(report['complete'] is complete and exit_code == (0 if complete else 2))
    return {'complete': complete, 'cleanup_confirmed': True, 'query_names': list(expected)}


def read_final(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('duplicate_offline_report_field')
            value[key] = item
        return value
    def invalid(value): raise ValueError('invalid_offline_report_number')
    with Path(path).open('rb') as stream: data = stream.read(131073)
    if len(data) > 131072: raise ValueError('offline_report_oversize')
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'cli', 'init', 'output'): parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--seconds', type=float, default=180)
    parser.add_argument('--allocation-mode',choices=['global','objects'],default='global')
    args = parser.parse_args()
    result = probe(args.database, args.cli, args.init, args.output, args.nonce, seconds=args.seconds,allocation_mode=args.allocation_mode)
    raise SystemExit(0 if result['complete'] else 2)
