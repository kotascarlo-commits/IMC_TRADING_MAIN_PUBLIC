"""Bounded read-only observations for the canonical diagnostic supervisor."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]


def native_process_birth():
    """Read the current Windows process creation FILETIME without a subprocess."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, ended, system, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created),
            ctypes.byref(ended), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)


def record_scan(root, cycle_id, elapsed, evaluated, birth, *, observation=None):
    from .projection_io import write_json_projection
    write_json_projection(root/'RUNTIME/shadow-scan-status.json', {
        'schema_version': 1, 'updated_at': datetime.now(timezone.utc).isoformat(),
        'pid': os.getpid(), 'process_birth_filetime': birth, 'cycle_id': cycle_id,
        'cycle_seconds': elapsed, 'evaluated': evaluated, 'scan_success': True,
        'observation': observation or {},
    })


def age(value, now):
    result = (now-datetime.fromisoformat(value.replace('Z', '+00:00'))).total_seconds()
    if result < 0:
        raise ValueError('shadow_future_observation')
    return result


def scan_has_no_candidates(scan):
    """A running scanner is not enough: require successful discovery evidence."""
    observation = scan.get('observation') or {}
    providers = observation.get('providers') or {}
    return (type(scan.get('evaluated')) is int and scan['evaluated'] == 0
            and scan.get('scan_success') is True
            and observation.get('market_state') == 'NO_CANDIDATES'
            and observation.get('errors') == []
            and all(discovery_verified(providers.get(name, {})) for name in ('gecko', 'dex'))
            and (providers.get('refresh', {}).get('network_success') is True
                 or providers.get('refresh', {}).get('state') == 'NOT_REQUESTED'))


def discovery_verified(value):
    if (value.get('network_success') is True
            and value.get('state') in ('FRESH','DISCOVERY_CACHE_MARKET_REFRESH')):
        return True
    age=value.get('empty_age_seconds')
    return (value.get('state')=='CACHED_EMPTY' and value.get('empty_verified') is True
            and type(age) in (int,float) and 0<=age<=10)


def storage_observation(root=ROOT):
    paths = [root/'CORE/data/imc-main.db', root/'RUNTIME/watch-breadth-v425-shadow.db',
             root/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db']
    return {'storage_bytes': sum(p.stat().st_size for base in paths for p in
            (base, Path(str(base)+'-wal'), Path(str(base)+'-shm')) if p.exists()),
            'disk_free_bytes': shutil.disk_usage(root).free}


def observe(root=ROOT, *, now=None):
    now = now or datetime.now(timezone.utc)
    path = root/'CORE/data/imc-main.db'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)) as c:
        c.execute('PRAGMA query_only=ON')
        rows = c.execute('SELECT id,payload_json FROM snapshots ORDER BY id DESC LIMIT 100').fetchall()
    if not rows:
        raise ValueError('shadow_no_snapshots')
    docs = [json.loads(raw) for _, raw in rows]
    latest_age = age(docs[0]['captured_at'], now)
    security = [d.get('security') or {} for d in docs]
    known = lambda s: (type(s.get('top10_holder_pct')) in (int, float)
                       and type(s.get('creator_holder_pct')) in (int, float))
    watch = json.loads((root/'RUNTIME/shadow-watch-status.json').read_text(encoding='utf-8-sig'))
    watch_age = age(watch['updated_at'], now)
    breadth = json.loads((root/'RUNTIME/watch_breadth_v425_shadow_status.json').read_text(encoding='utf-8-sig'))
    breadth_age = age(breadth['updated'], now)
    no_candidates = False
    try:
        scan = json.loads((root/'RUNTIME/shadow-scan-status.json').read_text(encoding='utf-8-sig'))
        no_candidates = scan_has_no_candidates(scan) and age(scan['updated_at'], now) <= 30
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {'schema_version': 1, 'evidence_domain': 'DIAGNOSTIC_SHADOW_NOT_PERFORMANCE',
        'observed_at': now.isoformat(), 'latest_id': rows[0][0], 'source_age_seconds': latest_age,
        'watch_age_seconds': watch_age, 'breadth_age_seconds': breadth_age,
        'feed_complete': watch['feed_complete'] is True,
        'watch_errors': watch['shadow_errors'],
        'shadow_targets': watch['shadow_targets'], 'shadow_observed': watch['shadow_observed'],
        'breadth_error': bool(breadth.get('last_error')),
        'sample_size': len(docs), 'security_checked': sum(s.get('checked') is True for s in security),
        'security_timestamp_present': sum(s.get('checked_at') is not None for s in security),
        'holder_values_present': sum(known(s) for s in security),
        'no_candidates': no_candidates,
        'qualified_trading_ready': False,
        'automatic_learning_suppressed': os.environ.get('IMC_SHADOW_DATA_ONLY') == '1',
        **storage_observation(root)}


def main():
    try:
        result = observe()
    except Exception as error:
        # Do not project URLs, arbitrary exception text or response bodies.
        result = {'schema_version': 1, 'error': type(error).__name__}
    print(json.dumps(result, separators=(',', ':')))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(main())
