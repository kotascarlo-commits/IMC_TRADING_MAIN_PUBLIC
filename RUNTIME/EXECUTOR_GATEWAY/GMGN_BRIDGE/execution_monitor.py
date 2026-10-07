"""Owned gateway read loop. Read readiness never authorizes an order send."""
import threading
import time
import sqlite3
import json
import os
import re
import importlib.util
from copy import deepcopy
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

from execution_state import StateError, timestamp, age
from execution_evidence import ReconciliationReader, RPCError, RPCTransportError, RPCSnapshotDriftError
from mykonos_meme_scout.projection_io import write_json_projection

_SYSTEM_HEALTH = None
RUNTIME_ROOT = Path(__file__).resolve().parents[2]


def runtime_health(store, *, deadline):
    """Reuse native health inspection; saved PASS alone is never a probe."""
    global _SYSTEM_HEALTH
    root = RUNTIME_ROOT
    if _SYSTEM_HEALTH is None:
        spec = importlib.util.spec_from_file_location('imc_owned_health', root / 'imc_system_health.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SYSTEM_HEALTH = module
    health = _SYSTEM_HEALTH
    now, limit = store.clock(), store.cfg['reconciliation_max_age_seconds']
    result = {'heartbeats': {name: {'status': 'UNKNOWN', 'observed_at': now,
        'source': 'gateway:native-runtime-health'} for name in ('CORE', 'MONITORING')},
        'watchdog': {'status': 'UNKNOWN', 'observed_at': now, 'source': 'gateway:native-supervisor'}}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return result
    config = health.load_config()
    snapshot = health.process_snapshot(min(config['process_timeout_seconds'], remaining))
    if snapshot.get('error') or snapshot.get('unknown_interpreter_pids'):
        return result
    def birth(created):
        if isinstance(created, str) and re.fullmatch(r'/Date\([0-9]+\)/', created):
            created = datetime.fromtimestamp(int(created[6:-2])/1000, timezone.utc).isoformat()
        age(created, store.clock())
        return created
    roles = {}
    for process in snapshot['processes']:
        role = process['Role']
        if role in roles:
            return result
        created = birth(process.get('CreationDate'))
        if type(process['ProcessId']) is not int or process['ProcessId'] <= 0:
            return result
        roles[role] = (process['ProcessId'], created)
    expected = {'CORE': 'Core', 'WATCHER': 'Watcher', 'GATEWAY': 'PureSignalGateway',
                'BRIDGE': 'Bridge', 'HEALTH': 'Health'}
    if (any(role not in roles for role in (*expected.values(), 'SignalSupervisor'))
            or roles['PureSignalGateway'][0] != os.getpid()):
        return result
    # Read bounded projections, then bind their PIDs and publication time to
    # the independently observed native generations. Neither file grants SEND.
    def read(path):
        with path.open('rb') as stream:
            body = stream.read(524289)
        if len(body) > 524288:
            raise StateError('runtime_health_projection_size')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise StateError('runtime_health_duplicate_field')
                value[key] = item
            return value
        return json.loads(body.decode('utf-8-sig'), object_pairs_hook=unique)
    supervisor = read(root / 'signal-runtime-status.json')
    report = read(root / 'IMC_SYSTEM_HEALTH.json')
    published = supervisor['updated_at']
    from execution_validation import require as validation_contract
    with store.transaction(deadline=deadline) as c:
        validation=validation_contract(store,c)
        expected_mode=validation['mode'] if validation else 'SIGNAL_ONLY'
        wallet=store.meta(c,'wallet')
    if validation and (validation['domain']!='FINALIZED_CHAIN' or
            supervisor.get('execution_contract_identity')!=validation['identity'] or supervisor.get('wallet')!=wallet):
        return result
    if (supervisor['state'] != 'RUNNING' or supervisor['pid'] != roles['SignalSupervisor'][0]
            or supervisor.get('broadcast') is not store.cfg['broadcast_enabled'] or supervisor.get('live_ready') is not False
            or supervisor.get('execution_mode') != expected_mode
            or type(supervisor['pid']) is not int
            or report.get('configured_mode') != 'PURE_SIGNAL_ONLY'
            or any(role in roles for role in config['modes']['PURE_SIGNAL_ONLY']['conflicting_roles'])
            or age(published, store.clock()) > limit
            or age(report['updated'], store.clock()) > limit):
        return result
    age(roles['SignalSupervisor'][1], published)
    if set(supervisor['workers']) != set(expected):
        return result
    # The supervisor owns the venv launcher; the canonical native inspector
    # already binds that launcher to its unique actual worker using held handles.
    # Consume that proof, preserving both generations and any console binding.
    def redirects(source):
        records = source.get('interpreter_redirectors', [])
        if not isinstance(records, list):
            raise StateError('runtime_redirectors_unknown')
        bindings = {}
        for entry in records:
            parent, worker, role = entry.get('ProcessId'), entry.get('WorkerPid'), entry.get('Role')
            if (type(parent) is not int or parent <= 0 or type(worker) is not int or worker <= 0
                    or parent == worker or parent in bindings or role not in roles
                    or worker != roles[role][0] or entry.get('Evidence') != 'VENV_NATIVE_HANDLE_ARGV_BINDING'):
                raise StateError('runtime_redirector_binding_unknown')
            parent_birth = birth(entry.get('CreationDate'))
            age(parent_birth, roles[role][1])
            console, console_birth = entry.get('ConsolePid'), entry.get('ConsoleCreated')
            if console is not None:
                if type(console) is not int or console <= 0 or console in (parent, worker):
                    raise StateError('runtime_redirector_console_unknown')
                console_birth = birth(console_birth)
                age(parent_birth, console_birth)
            elif console_birth is not None:
                raise StateError('runtime_redirector_console_unknown')
            times = [parent_birth, roles[role][1], *([console_birth] if console is not None else [])]
            for created in times:
                age(created, published)
                age(created, report['updated'])
            bindings[parent] = (role, parent_birth, worker, roles[role][1],
                                entry['Evidence'], console, console_birth)
        if len({b[2] for b in bindings.values()}) != len(bindings):
            raise StateError('runtime_redirector_ambiguous_worker')
        return bindings
    native_redirects = redirects(snapshot)
    saved_redirects = redirects(report['process_snapshot'])
    if native_redirects != saved_redirects:
        return result
    for key, role in expected.items():
        worker = supervisor['workers'][key]['pid']
        if (type(worker) is not int or worker <= 0 or (worker != roles[role][0]
                and (worker not in native_redirects or native_redirects[worker][0] != role))):
            return result
        age(roles[role][1], published)
        age(roles[role][1], report['updated'])
    reported = report['process_snapshot']
    if reported.get('error') or reported.get('unknown_interpreter_pids'):
        return result
    recorded = {p['Role']: p for p in reported['processes']}
    if len(recorded) != len(reported['processes']):
        return result
    for role in (*expected.values(), 'SignalSupervisor'):
        entry = recorded.get(role, {})
        created = birth(entry.get('CreationDate'))
        if (entry.get('ProcessId'), created) != roles[role]:
            return result
        age(created, report['updated'])
    age(roles['Health'][1], report['updated'])
    observed = min(published, report['updated'], key=lambda t: datetime.fromisoformat(t))
    core = health.db_probe(root.parent / 'CORE/data/imc-main.db', 'snapshots', True, limit, deadline=deadline)
    if core.get('readable') is True and core.get('error') is None:
        times = [core.get('last_snapshots_at'), core.get('last_decisions_at')]
        if all(age(t, store.clock()) <= limit for t in times):
            result['heartbeats']['CORE'].update(status='PASS', observed_at=min(
                [observed, *times], key=lambda t: datetime.fromisoformat(t)))
    if time.monotonic() >= deadline:
        raise StateError('runtime_health_deadline')
    result['heartbeats']['MONITORING'].update(status='PASS', observed_at=observed)
    result['watchdog'].update(status='PASS', observed_at=observed)
    return result


class ReadMonitor:
    STOP_WAIT_SECONDS = 3

    def __init__(self, store, status_path, *, reader_factory=None, publisher=None, monotonic=time.monotonic,
                 health_reader=None):
        self.store, self.status_path = store, Path(status_path)
        self.reader_factory = reader_factory or ReconciliationReader
        self.monotonic = monotonic
        self.publisher = publisher
        self.health_reader = health_reader
        self._runtime_health = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._health_lock = threading.Lock()
        self._lease = None
        self._shutdown_timeout = False
        self._pending_errors = deque(maxlen=store.cfg['max_api_failures'])
        from execution_validation import require
        with store.transaction() as c:contract=require(store,c)
        self._state = {'updated_at': None, 'observation_at': None, 'read_state': 'UNKNOWN',
                       'equity_state': 'UNKNOWN', 'last_error': None,
                       'rpc_code': None, 'rpc_context_slot': None, 'http_status': None,
                       'execution_mode': contract['mode'] if contract else 'SIGNAL_ONLY',
                       'live_ready': False, 'broadcast': store.cfg['broadcast_enabled']}

    def tick(self):
        # HTTP and periodic reads share the same lock; no queued catch-up reads.
        if self._stop.is_set():
            raise StateError('monitor_stopped')
        if not self._lock.acquire(blocking=False):
            raise StateError('reconciliation_busy')
        try:
            if self._stop.is_set():
                raise StateError('monitor_stopped')
            started_mono, last_wall = self.monotonic(), self.store.clock()
            last_mono = started_mono
            deadline = started_mono + self.store.cfg['reconciliation_max_age_seconds']
            observation = None
            with self._health_lock:
                self._lease = None
                self._runtime_health = None
            def fresh():
                nonlocal last_wall, last_mono
                mono, wall = self.monotonic(), self.store.clock()
                if self._stop.is_set():
                    raise StateError('monitor_stopped')
                age(last_wall, wall)
                if mono < last_mono or mono >= deadline:
                    raise StateError('reconciliation_read_deadline')
                last_wall, last_mono = wall, mono
                if observation is not None:
                    self.store.reconciliation_fresh(observation, deadline)
            def current():
                # The exact audit matcher and status see the same revision.
                with self.store.transaction(deadline=deadline, before_commit=fresh) as c:
                    fresh()
                    coherent = self.store.reconciliation_matches(c, observation)
                    status = self.store.status(connection=c, expire=False)
                    return coherent, status
            self._state.update(read_state='UNKNOWN', equity_state='UNKNOWN', last_error=None, error_code=None,
                               rpc_code=None, rpc_context_slot=None, http_status=None)
            try:
                self._flush_failures(deadline)
                observation = self.reader_factory(self.store.cfg).snapshot(self.store, deadline=deadline)
                fresh()
                ok = self.store.reconcile(observation, deadline=deadline, read_guard=fresh)
                coherent, status = current()
                self._state.update(observation_at=observation['observed_at'],
                                   read_state='VERIFIED' if coherent else 'UNRESOLVED')
                # Empty chain/executor/ledger plus resolved orders is an exact
                # native equity mark. Open positions require executable quotes.
                from execution_validation import require as validation_contract
                with self.store.transaction(deadline=deadline) as transaction:
                    validation=validation_contract(self.store,transaction)
                native_only = (not observation['chain_positions'] and not observation['executor_positions']) or bool(validation)
                if (coherent and native_only
                        and not status['open_positions']
                        and all(e['state'] in {'READY', 'REJECTED', 'FILLED'} for e in status['events'])):
                    self.store.mark_equity({'observed_at': observation['observed_at'], 'wallet': observation['wallet'],
                        'balance_lamports': observation['balance_lamports'], 'marks': {},
                        'reconciliation_revision': status['reconciliation_revision']}, deadline=deadline, read_guard=fresh)
                    coherent, status = current()
                    self._state['read_state'] = 'VERIFIED' if coherent else 'UNRESOLVED'
                    if (coherent and status['equity'] is not None and status['equity']['fresh']
                            and status['equity']['observed_at'] == observation['observed_at']):
                        self._state['equity_state'] = 'VERIFIED_NATIVE_ONLY'
                if self.health_reader is not None:
                    try:
                        probes = self.health_reader(self.store, deadline=deadline)
                        fresh()
                        with self._health_lock:
                            self._runtime_health = probes
                    except (OSError, ValueError, TypeError, KeyError, StateError):
                        self._state['runtime_health_error'] = 'RuntimeHealthUnavailable'
                    else:
                        self._state['runtime_health_error'] = None
                return ok
            except Exception as error:
                # No error text, URLs, provider bodies, headers or credentials.
                self._state['last_error'] = type(error).__name__
                safe_codes={'rpc_read_deadline','rpc_worker_response_invalid','rpc_base_interpreter_invalid',
                            'invalid_timestamp','reconciliation_read_deadline','monitor_stopped',
                            'ledger_lock_deadline','reconciliation_changed','reconciliation_expired',
                            'position_policy_unknown','validation_contract_corrupt'}
                self._state['error_code']=(str(error) if type(error) is StateError and str(error) in safe_codes
                                           else 'STATE_UNAVAILABLE' if isinstance(error,StateError) else None)
                self._state['read_state'] = 'UNKNOWN'
                details = {'rpc_code': error.code if isinstance(error, RPCError) else None,
                           'rpc_context_slot': error.context_slot if isinstance(error, RPCError) else None,
                           'http_status': error.http_status if isinstance(error, RPCTransportError) else None}
                self._state.update(details)
                if not self._stop.is_set():
                    temporary=(isinstance(error,RPCSnapshotDriftError)
                        or type(error) is StateError and str(error) in {'rpc_read_deadline','reconciliation_read_deadline'}
                        or isinstance(error,RPCError) and error.code in {-32016,-32005,-32603}
                        or isinstance(error,RPCTransportError) and (error.kind in {'TIMEOUT','NETWORK'}
                            or error.kind=='HTTP_STATUS' and error.http_status in {429,502,503,504}))
                    self._pending_errors.append((type(error).__name__, details,temporary))
                    try:
                        self._flush_failures(deadline)
                    except Exception:
                        pass  # retain bounded obligations for the next owned tick
                raise
            finally:
                self._state['updated_at'] = timestamp()
                try:
                    if self.publisher is not None:
                        self.publisher(self.store, deadline=deadline)
                    fresh()
                except (OSError, StateError, sqlite3.Error):
                    self._state.update(read_state='UNKNOWN', last_error='ProjectionUnavailable')
                if self._stop.is_set():
                    self._stopped_state()
                try:
                    write_json_projection(self.status_path, self._state,
                                          retry_seconds=min(.1, max(0, deadline-self.monotonic())))
                    fresh()
                    if self._state['read_state'] == 'VERIFIED':
                        with self._health_lock:
                            self._lease = (deadline, observation['observed_at'], last_wall, last_mono)
                except OSError:
                    self._state.update(read_state='UNKNOWN', last_error='ProjectionUnavailable')
                except StateError:
                    self._state.update(read_state='UNKNOWN', equity_state='UNKNOWN')
                if self._stop.is_set():
                    self._stopped_state()
        finally:
            self._lock.release()

    def _flush_failures(self, deadline):
        while self._pending_errors:
            reason, details, temporary = self._pending_errors[0]
            self.store.api_failure(reason, details=details, deadline=deadline,temporary=temporary)
            self._pending_errors.popleft()

    def start(self):
        if self._thread is not None or self._stop.is_set():
            raise StateError('monitor_already_started')
        self._thread = threading.Thread(target=self._run, name='imc-read-reconciliation', daemon=True)
        self._thread.start()

    def heartbeat(self):
        """Owned read health only; cached files never authorize execution."""
        with self._health_lock:
            result = dict(self._state)
            lease = self._lease
            valid = (lease is not None and self._thread is not None and self._thread.is_alive()
                     and not self._stop.is_set() and not self._lock.locked()
                     and result['read_state'] == 'VERIFIED')
            if valid:
                deadline, observed, wall, mono = lease
                try:
                    now_mono, now_wall = self.monotonic(), self.store.clock()
                    age(wall, now_wall)
                    valid = (mono <= now_mono < deadline
                             and age(observed, now_wall) <= self.store.cfg['reconciliation_max_age_seconds'])
                    if valid:
                        self._lease = (deadline, observed, now_wall, now_mono)
                except StateError:
                    valid = False
                if not valid:
                    self._lease = None
            result['status'] = 'PASS' if valid else 'UNKNOWN'
            if valid and self._runtime_health is not None:
                probes = deepcopy(self._runtime_health)
                for probe in [*probes['heartbeats'].values(), probes['watchdog']]:
                    try:
                        if age(probe['observed_at'], self.store.clock()) > self.store.cfg['reconciliation_max_age_seconds']:
                            probe['status'] = 'UNKNOWN'
                    except StateError:
                        probe['status'] = 'UNKNOWN'
                result['runtime_health'] = probes
            return result

    def _run(self):
        cadence = self.store.cfg['reconciliation_poll_seconds']
        while not self._stop.is_set():
            started = self.monotonic()
            try:
                self.tick()
            except Exception:
                # The next independent observation may restore read health;
                # persistent wallet/ledger conflicts remain guarded by SQLite.
                pass
            self._stop.wait(max(.1, cadence - (self.monotonic() - started)))

    def stop(self):
        self._stop.set()
        deadline = time.monotonic() + self.STOP_WAIT_SECONDS
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0, deadline-time.monotonic()))
        # HTTP ticks own the same lock. A joined periodic thread alone does not
        # prove that every reader has finished. Never hold it during JOIN.
        idle = self._lock.acquire(timeout=max(0, deadline-time.monotonic()))
        if idle:
            self._lock.release()
        alive = self._thread is not None and self._thread.is_alive()
        if not idle or alive:
            self._shutdown_timeout = True
        self._stopped_state()
        if not idle or alive:
            raise StateError('monitor_shutdown_timeout')

    def _stopped_state(self):
        with self._health_lock:
            self._lease = None
            self._runtime_health = None
            self._state.update(read_state='UNKNOWN', equity_state='UNKNOWN')
            if self._shutdown_timeout:
                self._state['last_error'] = 'ReadMonitorShutdownTimeout'
