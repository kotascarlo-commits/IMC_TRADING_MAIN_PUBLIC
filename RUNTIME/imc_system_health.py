from __future__ import annotations

import argparse
import json
import math
import msvcrt
import os
import re
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RUNTIME = BASE / 'RUNTIME'
OUT = RUNTIME / 'IMC_SYSTEM_HEALTH.json'
LOCK = RUNTIME / 'IMC_SYSTEM_HEALTH.lock'
HEALTH_CONFIG = BASE / 'config/imc-health.json'
MASTER_MODE = RUNTIME / 'EXECUTOR_GATEWAY/IMC_MASTER_MODE.json'
RATE_DB = RUNTIME / 'imc-resilience.db'


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def load_config() -> dict:
    config = json.loads(HEALTH_CONFIG.read_text(encoding='utf-8-sig'))
    if config['schema_version'] != 1:
        raise ValueError('Unsupported health config')
    for key in ('process_timeout_seconds', 'core_stale_seconds', 'poll_seconds'):
        if not _positive(config[key]):
            raise ValueError(f'Invalid {key}')
    if not config['modes'] or not config['components']:
        raise ValueError('Health modes/components missing')
    if config['components']['core']['role'] != 'Core':
        raise ValueError('Core health component missing')
    for mode in config['modes'].values():
        for key in ('expected_roles', 'conflicting_roles'):
            roles = mode[key]
            if not isinstance(roles, list) or any(not isinstance(r, str) or not r for r in roles):
                raise ValueError('Invalid mode roles')
            if len(roles) != len(set(roles)) or (key == 'expected_roles' and not roles):
                raise ValueError('Empty/duplicate expected roles')
    for component in config['components'].values():
        if not isinstance(component['role'], str) or not component['role']:
            raise ValueError('Invalid component role')
        if 'status' in component and component['stale_seconds'] is not None and not _positive(component['stale_seconds']):
            raise ValueError('Invalid status threshold')
        for key in ('status', 'database'):
            if key in component:
                (BASE / component[key]).resolve().relative_to(BASE.resolve())
        if 'database' in component and not isinstance(component['table'], str):
            raise ValueError('Missing database table')
    return config


def process_snapshot(timeout: float) -> dict:
    helper = str(BASE / 'LAUNCHERS/IMC_PROCESS_COMMON.ps1').replace("'", "''")
    command = ("$ErrorActionPreference='Stop'; . '" + helper + "'; "
               "$s=Get-IMCProcessSnapshot; [pscustomobject]@{processes=@($s.Processes | "
               "Select-Object Role,ProcessId,CreationDate,@{n='CreationFiletime';e={$_.CreationDate.ToFileTimeUtc().ToString()}}); interpreter_redirectors=@($s.InterpreterRedirectors | "
               "Select-Object Role,ProcessId,CreationDate,WorkerPid,Evidence,ConsolePid,ConsoleCreated); "
               "unknown_interpreter_pids=@($s.UnknownInterpreterPids); "
               "unknown_interpreter_processes=@($s.UnknownInterpreterProcesses); "
               "expired_process_evidence=@($s.ExpiredProcessEvidence); "
               "snapshot_at=$s.SnapshotAt;snapshot_id=$s.SnapshotId} "
               "| ConvertTo-Json -Compress -Depth 6")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                                check=True)
        snapshot = json.loads(result.stdout)
        if not isinstance(snapshot['processes'], list) or not isinstance(snapshot['unknown_interpreter_pids'], list):
            raise ValueError('Malformed process snapshot')
        for row in snapshot['processes']:
            if not isinstance(row['Role'], str) or not isinstance(row['ProcessId'], int):
                raise ValueError('Malformed process identity')
        if not isinstance(snapshot['interpreter_redirectors'], list):
            raise ValueError('Malformed redirector list')
        for row in snapshot['interpreter_redirectors']:
            if (type(row.get('ProcessId')) is not int or row['ProcessId'] <= 0
                    or type(row.get('WorkerPid')) is not int or row['WorkerPid'] <= 0
                    or row.get('Evidence') != 'VENV_NATIVE_HANDLE_ARGV_BINDING'):
                raise ValueError('Malformed redirector identity')
        snapshot['error'] = None
        snapshot['visibility'] = 'PARTIAL' if snapshot['unknown_interpreter_pids'] else 'RECOGNIZED_ABSOLUTE_PATHS'
        return snapshot
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return {'processes': [], 'unknown_interpreter_pids': [], 'visibility': 'UNKNOWN', 'error': str(exc)}


def _age_seconds(timestamp) -> float | None:
    try:
        value = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if value.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - value).total_seconds()
        return age if age >= 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def scan_status(snapshot):
    result = {'verified': False, 'error': 'SCAN_HEARTBEAT_UNVERIFIED'}
    try:
        path = RUNTIME/'shadow-scan-status.json'
        if path.stat().st_size > 65536:
            raise ValueError('scan size')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        core = [p for p in snapshot['processes'] if p['Role'] == 'Core']
        if len(core) != 1 or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
            raise ValueError('scan identity')
        for name in ('pid', 'cycle_id', 'evaluated'):
            if type(value.get(name)) is not int or value[name] < (0 if name == 'evaluated' else 1):
                raise ValueError('scan number')
        birth = value.get('process_birth_filetime')
        actual = core[0].get('CreationFiletime')
        if (not isinstance(birth, str) or not re.fullmatch(r'[1-9][0-9]{17}', birth)
                or not isinstance(actual, str) or not re.fullmatch(r'[1-9][0-9]{17}', actual)
                or abs(int(birth)-int(actual)) >= 10 or value['pid'] != core[0]['ProcessId']
                or value.get('scan_success') is not True):
            raise ValueError('scan identity')
        duration = value.get('cycle_seconds')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError('scan duration')
        age = _age_seconds(value.get('updated_at'))
        stamp = datetime.fromisoformat(value['updated_at'].replace('Z', '+00:00')).timestamp()
        if age is None or age > 30 or stamp < (int(birth)-116444736000000000)/10**7:
            raise ValueError('scan freshness')
        return {**value, 'verified': True, 'error': None, 'age_seconds': age}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return result


def _read_rows(path: Path, sql: str, *, deadline=None) -> list:
    deadline = min(time.monotonic() + 2, deadline if deadline is not None else float('inf'))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('health_read_deadline')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=remaining)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return connection.execute(sql).fetchall()


def parse_status(path: Path, stale_seconds: float | None) -> dict:
    result = {'exists': path.is_file(), 'stale': None, 'age_seconds': None,
              'evidence': 'SAVED_FILE_ONLY', 'last_error': None, 'saved_live_execution': None}
    if not result['exists']:
        return result
    try:
        age = time.time() - path.stat().st_mtime
        result['age_seconds'] = round(age, 1)
        result['stale'] = age < 0 or (stale_seconds is not None and age > stale_seconds)
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('Status must be an object')
        result['saved_live_execution'] = data.get('live_execution')
        result['saved_authority'] = data.get('authority')
        result['last_error'] = data.get('last_error')
        result['saved_state'] = data.get('state')
    except (OSError, ValueError) as exc:
        result['last_error'] = f'STATUS_PARSE: {exc}'
    return result


def db_probe(path: Path, table: str, core: bool, stale_seconds: float, *, deadline=None) -> dict:
    result = {'exists': path.is_file(), 'readable': False, 'error': None}
    try:
        identifier = '"' + table.replace('"', '""') + '"'
        _read_rows(path, f'SELECT 1 FROM {identifier} LIMIT 1', deadline=deadline)
        result['readable'] = True
        if core:
            for name in ('snapshots', 'decisions'):
                rows = _read_rows(path, f'SELECT captured_at FROM {name} ORDER BY id DESC LIMIT 1', deadline=deadline)
                timestamp = rows[0][0] if rows else None
                age = _age_seconds(timestamp)
                result[f'last_{name}_at'] = timestamp
                result[f'{name}_age_seconds'] = round(age, 1) if age is not None else None
                result[f'{name}_stale'] = age is None or age > stale_seconds
    except (OSError, sqlite3.Error) as exc:
        result['error'] = str(exc)
    return result


def rate_snapshot() -> dict:
    try:
        rows = _read_rows(RATE_DB, 'SELECT name,next_allowed,updated_at,reason FROM rate_buckets')
        return {'known': True, 'budgets': {str(name): {'wait_seconds': round(max(0, float(slot)-time.time()), 3),
                'updated_at_epoch': updated, 'reason': reason} for name, slot, updated, reason in rows}, 'error': None}
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return {'known': False, 'budgets': None, 'error': str(exc)}


def recent_execution_errors(path: Path) -> dict:
    try:
        rows = _read_rows(path, 'SELECT status,COUNT(*) FROM (SELECT status FROM trades ORDER BY id DESC LIMIT 50) GROUP BY status')
        return {'last_50': {str(status): count for status, count in rows}, 'error': None}
    except (OSError, sqlite3.Error) as exc:
        return {'last_50': None, 'error': str(exc)}


def x_context_source() -> dict:
    configured = os.getenv('IMC_X_CONTEXT_DB')
    candidates = [Path(configured)] if configured else []
    candidates += [RUNTIME / 'x_to_minds.db', BASE / 'CORE/data/x_to_minds.db']
    found = [path for path in candidates if path.is_file()]
    return {'configured': bool(found),
            'path': str(max(found, key=lambda path: path.stat().st_mtime)) if found else None}


def build_report() -> dict:
    report = {'version': '2.0', 'updated': now_iso(), 'verdict': 'DEGRADED',
              'authority_guard': 'UNKNOWN', 'live_execution_any': None,
              'execution_evidence': 'Process and saved-file inspection does not prove broadcast or its absence.',
              'warnings': [], 'critical': [], 'info': [], 'statuses': {}, 'databases': {}, 'execution_errors': {}}
    warnings, critical = report['warnings'], report['critical']
    try:
        config = load_config()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        warnings.append(f'health_config: {exc}')
        return report
    try:
        mode_file = json.loads(MASTER_MODE.read_text(encoding='utf-8-sig'))
        report['source_master_mode'] = mode_file['mode']
        override = os.environ.get('IMC_HEALTH_MODE')
        if override not in (None, 'DATA_ONLY_SHADOW'):
            raise ValueError('Unsupported diagnostic health override')
        mode = override or mode_file['mode']
        rule = config['modes'][mode]
        if any(mode_file.get(key) is not False for key in ('live_broadcast', 'signing', 'wallet_access')):
            critical.append('master_mode: safe flags missing or enabled; configuration only, requires investigation')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        warnings.append(f'master_mode: unknown/unconfigured mode: {exc}')
        mode, rule = None, {'expected_roles': [], 'conflicting_roles': []}
    report['configured_mode'] = mode
    snapshot = process_snapshot(config['process_timeout_seconds'])
    report['process_snapshot'] = snapshot
    if mode == 'DATA_ONLY_SHADOW':
        report['scan_status'] = scan_status(snapshot)
        if not report['scan_status']['verified']:
            critical.append('core: scan heartbeat missing, stale or identity unverified')
    roles = Counter(row['Role'] for row in snapshot['processes'])
    if snapshot['error'] or snapshot['unknown_interpreter_pids']:
        warnings.append('process visibility incomplete; absence cannot be established')
    for role in rule['expected_roles']:
        if not roles[role]:
            critical.append(f'{role}: expected process not observed; not proof of absence')
    for role, count in roles.items():
        if count > 1:
            critical.append(f'{role}: duplicate observed processes ({count})')
        if role in rule['conflicting_roles']:
            critical.append(f'{role}: conflicts with configured mode {mode}')
    selected = set(rule['expected_roles']) | set(roles)
    for name, component in config['components'].items():
        if component['role'] not in selected:
            continue
        if 'status' in component:
            status = parse_status(BASE / component['status'], component['stale_seconds'])
            report['statuses'][name] = status
            if not status['exists'] or status['stale'] or status['last_error']:
                (critical if mode == 'DATA_ONLY_SHADOW' else warnings).append(f'{name}: saved status missing, stale or invalid')
            if status['saved_live_execution'] is True:
                critical.append(f'{name}: saved file reports live execution; investigate, not current-state proof')
        if 'database' in component:
            database = db_probe(BASE / component['database'], component['table'], name == 'core', config['core_stale_seconds'])
            report['databases'][name] = database
            if not database['readable'] or database['error']:
                (critical if mode == 'DATA_ONLY_SHADOW' else warnings).append(f'{name}: database/schema unavailable')
            required = ('snapshots',) if mode == 'DATA_ONLY_SHADOW' else ('snapshots', 'decisions')
            if name == 'core' and any(database.get(f'{table}_stale', True) for table in required):
                from mykonos_meme_scout.shadow_session import scan_has_no_candidates
                scan = report.get('scan_status') or {}
                if (mode == 'DATA_ONLY_SHADOW' and scan.get('verified') is True
                        and scan_has_no_candidates(scan)):
                    report['market_state'] = 'NO_CANDIDATES_ENTRY_PAUSED'
                    warnings.append('core: successful empty feeds; historical snapshots stale, entries paused')
                else:
                    critical.append('core: data freshness invalid/stale; evaluated separately from process presence')
            if name == 'core' and mode == 'DATA_ONLY_SHADOW' and database.get('decisions_stale', True):
                warnings.append('core: no recent decisions; scanner progress checked through bound heartbeat')
            if component['role'] in ('ExecutionTwin', 'VR12Execution'):
                report['execution_errors'][name] = recent_execution_errors(BASE / component['database'])
    report['jupiter_rate_budgets'] = rate_snapshot()
    if not report['jupiter_rate_budgets']['known']:
        warnings.append('rate budgets: unavailable/unknown')
    report['x_context'] = x_context_source()
    if not report['x_context']['configured']:
        report['info'].append('Optional X-context source unavailable; adjustment remains neutral.')
    report['info'].append('Only recognized absolute script/module paths are identified; other argument forms may be missed.')
    report['verdict'] = 'CRITICAL' if critical else ('DEGRADED' if warnings else 'HEALTHY')
    return report


def write_atomic(data: dict) -> None:
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(OUT)


def acquire_singleton():
    handle = open(LOCK, 'a+b')
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(description='Mode-aware health; process visibility and data freshness are separate.')
    parser.add_argument('--once', action='store_true', help='Print read-only JSON without lock or output file writes')
    args = parser.parse_args()
    if args.once:
        print(json.dumps(build_report(), indent=2))
        return
    singleton = acquire_singleton()
    if singleton is None:
        return
    try:
        while True:
            try:
                write_atomic(build_report())
                interval = load_config()['poll_seconds']
            except Exception as exc:
                write_atomic({'version': '2.0', 'updated': now_iso(), 'verdict': 'DEGRADED',
                              'warnings': [f'health_guard: {type(exc).__name__}: {exc}']})
                interval = 5
            time.sleep(interval)
    finally:
        singleton.close()


if __name__ == '__main__':
    main()
