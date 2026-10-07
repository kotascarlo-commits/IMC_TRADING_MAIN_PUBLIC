"""Central fail-closed arming contract. This module never writes the owner gate.

It grants no transport capability: a production sender must enforce a fresh
decision immediately before its single SEND. Passive gateway claims remain OFF.
"""
import json
import math
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from execution_state import StateError, age, decimal, raw, timestamp

ROOT = Path(__file__).resolve().parents[3]
OWNER_GATE = ROOT / 'config/owner-broadcast-gate.json'
CRITICAL = ('CORE', 'MARKET_DATA', 'GMGN_API', 'EXECUTION_ADAPTER', 'RPC',
            'RECONCILIATION', 'WALLET_READER', 'RISK_ENGINE', 'POSITION_MANAGER', 'MONITORING')
CAPABILITIES = ('wallet_binding', 'order_build', 'buy_transport', 'sell_transport',
                'fill_verification', 'restart_recovery', 'watchdog')


@dataclass(frozen=True)
class ClaimLease:
    event_id: str
    request_id: str
    payload_hash: str
    payload_json: str


@dataclass(frozen=True)
class SendPermit:
    event_id: str
    payload_json: str
    issued_at: str
    valid_until: str
    monotonic_deadline: float
    request_id: str
    trade_id: str
    payload_hash: str
    guard_revision: str
    evidence_domain: str = 'FINALIZED_CHAIN'


def owner_gate(path=OWNER_GATE):
    """Missing, oversized, duplicate, malformed or unknown values are OFF."""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_gate_field')
            result[key] = value
        return result
    try:
        with Path(path).open('rb') as stream:
            body = stream.read(1025)
        if len(body) > 1024:
            raise ValueError('gate_size')
        value = json.loads(body.decode('utf-8-sig'), object_pairs_hook=unique_pairs)
        if (set(value) != {'schema_version', 'OWNER_BROADCAST_GATE'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['OWNER_BROADCAST_GATE'] not in ('OFF', 'ON')):
            raise ValueError('gate_schema')
        return {'value': value['OWNER_BROADCAST_GATE'], 'valid': True}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return {'value': 'OFF', 'valid': False}


def quality_gates(performance, equity, minimum_trades=100):
    """Closed reconciled forward evidence only; missing metrics never pass."""
    try:
        evidence = (performance.get('evidence_class') == 'FINALIZED_CHAIN_RECONCILED'
                    and isinstance(performance.get('source'), str) and bool(performance['source']))
        count = performance.get('count')
        return {'evidence': evidence,
                'sample': type(count) is int and count >= minimum_trades,
                'profit_factor': performance.get('profit_factor') is not None and decimal(performance['profit_factor']) >= 2,
                'hit_rate': performance.get('win_rate') is not None and decimal(performance['win_rate']) >= decimal('.70'),
                'drawdown': equity is not None and decimal(equity['max_drawdown']) <= decimal('.15'),
                'top3_share': performance.get('top3_net_profit_share') is not None
                              and decimal(performance['top3_net_profit_share']) <= decimal('.25')}
    except (StateError, KeyError, TypeError):
        return {name: False for name in ('evidence', 'sample', 'profit_factor', 'hit_rate', 'drawdown', 'top3_share')}


class RiskEngine:
    def __init__(self, store, *, gate_path=OWNER_GATE, clock=timestamp, isolated_transport=None):
        store.pin_config()
        from execution_positions import initialize_policy
        with store.transaction() as c:
            initialize_policy(store, c)
        self.store, self.gate_path, self.clock = store, Path(gate_path), clock
        self._lock = threading.RLock()
        self._leases, self._approved, self._permits = {}, {}, {}
        if isolated_transport is not None:
            from execution_jupiter import IsolatedJupiterTransport
            from execution_validation import require
            with store.transaction() as c:
                contract = require(store, c)
            if (type(isolated_transport) is not IsolatedJupiterTransport or isolated_transport.store is not store
                    or not contract or contract['domain'] != 'ISOLATED'):
                raise StateError('isolated_transport_binding_invalid')
            store._isolated_transport = isolated_transport

    def reserve(self, event_id, quote, payload, *, provider='GMGN'):
        with self._lock:
            event = self.store.claim(event_id, quote, payload=payload, provider=provider)
            lease = ClaimLease(event_id, event['request_id'], event['payload_hash'], event['request_payload_json'])
            self._leases[event_id] = lease
            return event, lease

    def bind_jupiter_build(self, lease, prepared):
        with self._lock:
            with self.store.transaction() as c:
                event = self._lease_event(c, lease)
                if event is None:
                    raise StateError('jupiter_build_lease_invalid')
                from execution_jupiter import bind_build
                event = bind_build(self.store, c, event, prepared)
            rebound = ClaimLease(lease.event_id, event['request_id'], event['payload_hash'], event['request_payload_json'])
            self._leases[lease.event_id] = rebound
            self._approved.pop(lease.event_id, None)
            return event, rebound

    def _lease_event(self, c, lease):
        with self._lock:
            if type(lease) is not ClaimLease or self._leases.get(lease.event_id) is not lease:
                return None
            event = self.store.get(c, 'events', lease.event_id)
            if (not event or event['state'] != 'SENT' or event.get('send_started_at') is not None
                    or event.get('adapter_contract_version') not in (1, 2) or event.get('request_id') != lease.request_id
                    or event.get('payload_hash') != lease.payload_hash or event.get('request_payload_json') != lease.payload_json):
                return None
            return event

    def revoke(self, lease):
        with self._lock:
            if type(lease) is ClaimLease and self._leases.get(lease.event_id) is lease:
                self._leases.pop(lease.event_id, None)
                self._approved.pop(lease.event_id, None)

    def _record_approval(self, lease, decision, revision, issued, valid_until, monotonic_deadline):
        with self._lock:
            now = self.clock()
            try:
                age(issued, now)
                age(now, valid_until)
            except StateError:
                return False
            if self._leases.get(lease.event_id) is not lease or time.monotonic() >= monotonic_deadline:
                return False
            self._approved[lease.event_id] = (decision, revision, issued, valid_until, monotonic_deadline)
            return True

    def authorize_send(self, lease, decision):
        with self._lock:
            approved = self._approved.get(getattr(lease, 'event_id', None))
            if (type(lease) is not ClaimLease or self._leases.get(lease.event_id) is not lease
                    or not approved or approved[0] is not decision or decision.get('authorized') is not True
                    or decision.get('guard_revision') != approved[1] or decision.get('valid_until') != approved[3]
                    or decision.get('observed_at') != approved[2]):
                raise StateError('send_lease_not_authorized')
            # Consume before CAS/I/O; failure never permits a second dispatch.
            self._leases.pop(lease.event_id)
            self._approved.pop(lease.event_id, None)
            gate = owner_gate(self.gate_path)
            if not self.store.owner_send_allowed(lease.event_id, gate, deadline=approved[4]):
                raise StateError('owner_gate_off_before_send')
            event, revision = self.store.authorize_send(lease.event_id, lease.request_id, lease.payload_hash, approved[1],
                                      valid_from=approved[2], valid_until=approved[3],
                                      monotonic_deadline=approved[4], gate_path=self.gate_path)
            permit = SendPermit(lease.event_id, lease.payload_json, approved[2], approved[3], approved[4],
                                lease.request_id, event['trade_id'], lease.payload_hash, revision,
                                event.get('evidence_domain', 'FINALIZED_CHAIN'))
            self._permits[lease.event_id] = permit
            return permit

    @contextmanager
    def send_window(self, permit):
        """Recheck the original permit; freeze risk until the child starts SEND.

        This grants no new permit and never extends its original deadline.
        SQLite guards and the in-process valuation fault share this window.
        """
        if type(permit) is not SendPermit:
            raise StateError('send_permit_invalid')
        with self._lock:
            active_connection = None
            def fresh():
                gate = owner_gate(self.gate_path)
                now = self.clock()
                age(permit.issued_at, now)
                age(now, permit.valid_until)
                if time.monotonic() >= permit.monotonic_deadline:
                    raise StateError('dispatch_evidence_expired')
                if not self.store.owner_send_allowed(permit.event_id, gate,
                        connection=active_connection, deadline=permit.monotonic_deadline):
                    raise StateError('owner_gate_off_before_dispatch')
            fresh()
            with self.store.transaction(deadline=permit.monotonic_deadline, before_commit=fresh) as c:
                active_connection = c
                with self.store._valuation_lock:
                    fresh()
                    event = self.store.get(c, 'events', permit.event_id)
                    if (not event or event['state'] != 'SENT' or not event.get('send_started_at')
                            or event.get('request_id') != permit.request_id or event.get('trade_id') != permit.trade_id
                            or event.get('payload_hash') != permit.payload_hash
                            or event.get('request_payload_json') != permit.payload_json
                            or event.get('dispatch_valid_until') != permit.valid_until
                            or self.store.guard_revision(c) != permit.guard_revision):
                        raise StateError('dispatch_guard_changed')
                    fresh()
                    if event.get('provider') == 'JUPITER_V2':
                        from execution_jupiter import require_pre_send
                        require_pre_send(self.store, c, event)
                        if event.get('evidence_domain') != permit.evidence_domain:
                            raise StateError('dispatch_domain_mismatch')
                    yield dict(event, close_only_authorized=self.store.owner_exit_allowed(c, permit.event_id))

    def dispatch_contract(self, permit):
        with self._lock:
            if type(permit) is not SendPermit or self._permits.get(permit.event_id) is not permit:
                raise StateError('send_permit_invalid_or_consumed')
            self._permits.pop(permit.event_id)
            def fresh():
                gate = owner_gate(self.gate_path)
                now = self.clock()
                age(permit.issued_at, now)  # Clock rollback is invalid evidence.
                age(now, permit.valid_until)
                if time.monotonic() >= permit.monotonic_deadline:
                    raise StateError('dispatch_evidence_expired')
                if not self.store.owner_send_allowed(permit.event_id, gate, deadline=permit.monotonic_deadline):
                    raise StateError('owner_gate_off_before_dispatch')
            fresh()
            with self.send_window(permit) as event:
                pass
            fresh()
            def utc(value):
                date = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if date.tzinfo is None:
                    raise StateError('dispatch_timestamp_invalid')
                return date.astimezone(timezone.utc)
            issued_date, expiry_date = utc(permit.issued_at), utc(permit.valid_until)
            if not timedelta(0) < expiry_date-issued_date <= timedelta(seconds=2):
                raise StateError('dispatch_host_contract_invalid')
            # Claim starts the ledger's unresolved-order timeout. Observing an
            # ACK must never renew that budget from this later handoff time.
            timeout = decimal(self.store.cfg['request_timeout_seconds'])
            if not 0 < timeout <= 30:
                raise StateError('dispatch_response_budget_invalid')
            response_date = utc(event['sent_at']) + timedelta(microseconds=int(timeout*1000000))
            if not utc(event['sent_at']) <= issued_date < expiry_date <= response_date <= issued_date+timedelta(seconds=30):
                raise StateError('dispatch_response_budget_invalid')
            def millis(date, *, ceiling=False):
                delta = date - datetime(1970, 1, 1, tzinfo=timezone.utc)
                return delta.days*86400000 + delta.seconds*1000 + delta.microseconds//1000 + int(ceiling and delta.microseconds%1000 != 0)
            issued, expiry = millis(issued_date, ceiling=True), millis(expiry_date)
            response_end = millis(response_date)
            if (not 0 < expiry-issued <= 2000 or not 0 <= issued <= expiry < 2**53
                    or not expiry <= response_end < 2**53 or not 0 < response_end-issued <= 30000
                    or not all(re.fullmatch(r'[A-Za-z0-9_:.-]{1,128}', value)
                               for value in (permit.event_id, permit.request_id, permit.trade_id))):
                raise StateError('dispatch_host_contract_invalid')
            fresh()
            contract = {'eventId': permit.event_id, 'requestId': permit.request_id,
                'tradeId': permit.trade_id, 'payloadHash': permit.payload_hash, 'payloadJson': permit.payload_json,
                'issuedAtMs': issued, 'expiresAtMs': expiry, 'responseUntilMs': response_end}
            if event.get('provider') == 'JUPITER_V2':
                contract.update(provider='JUPITER_V2', evidenceDomain=permit.evidence_domain,
                                signature=event['order_id'], claimId=event['claim_id'],
                                routeIdentity=event['jupiter_prepared']['route_identity'])
            return MappingProxyType(contract)

    def dispatch_payload(self, permit):
        return self.dispatch_contract(permit)['payloadJson'].encode()

    def assess(self, *, heartbeats, capabilities, side='BUY', event=None, quote=None, lease=None):
        """Internal trusted probe input. No HTTP caller can assert these facts.

        Safety gates remain independent from owner authorization. OFF admits
        only verified exits of entries persistently authorized under Owner ON.
        """
        failures = []
        validation = None
        gate = owner_gate(self.gate_path)
        now = self.clock()
        expiries = []
        owned_deadlines = []
        close_only_authorized = False
        def expires(value, seconds):
            expiries.append(datetime.fromisoformat(value.replace('Z', '+00:00')) + timedelta(seconds=seconds))
        try:
            with self._lock, self.store.transaction() as c:
                from execution_positions import require_policy
                require_policy(self.store, c)
                status = self.store.status(connection=c)
                from execution_validation import status as validation_status
                validation = validation_status(self.store, c)
                exit_pending = self.store.exit_pending(c)
                revision = self.store.guard_revision(c)
                reconciled = self.store.meta(c, 'reconciled_at')
                owned = self._lease_event(c, lease) if lease is not None else None
                close_only_authorized = (side == 'SELL' and event is not None
                                         and self.store.owner_exit_allowed(c, event.get('event_id')))
                now = self.clock()  # Re-read after both potentially waiting locks.
            if lease is not None and owned is None:
                failures.append('claim_lease_invalid')
            if owned is not None:
                if event != owned or quote != owned.get('quote'):
                    failures.append('claim_payload_or_event_changed')
                event = owned
            cfg = self.store.cfg
            critical = CRITICAL if side != 'SELL' else tuple(c for c in CRITICAL if c not in {'CORE', 'MARKET_DATA'})
            if validation:
                critical = tuple('JUPITER_API' if item == 'GMGN_API' else item for item in critical)
                if validation['aborted'] or validation['stopped']:
                    failures.append('validation_stopped')
            for component in critical:
                probe = heartbeats.get(component, {})
                if component == 'POSITION_MANAGER':
                    original = probe.get('valid_until_monotonic')
                    if (type(original) not in (int, float) or not math.isfinite(original)
                            or original <= time.monotonic()):
                        failures.append('heartbeat:POSITION_MANAGER')
                    else:
                        owned_deadlines.append(original)
                        expires(now, max(0, original - time.monotonic()))
                if (probe.get('status') != 'PASS' or not probe.get('source')
                        or age(probe.get('observed_at'), now) > cfg['reconciliation_max_age_seconds']):
                    failures.append('heartbeat:' + component)
                else:
                    expires(probe['observed_at'], cfg['reconciliation_max_age_seconds'])
            for name in CAPABILITIES:
                if capabilities.get(name) is not True:
                    failures.append('capability:' + name)
            if not gate['valid']:
                failures.append('owner_gate_invalid')
            if side not in ('BUY', 'SELL'):
                failures.append('invalid_side')
            if status['reconciliation_required'] or not status.get('reconciliation_revision'):
                failures.append('reconciliation_required')
            if not reconciled or age(reconciled, now) > cfg['reconciliation_max_age_seconds']:
                failures.append('stale_reconciliation')
            else:
                expires(reconciled, cfg['reconciliation_max_age_seconds'])
            if any(e['state'] in {'SENT', 'ACK', 'PARTIAL', 'UNKNOWN'}
                   and not (owned is not None and e['event_id'] == owned['event_id'] and e['state'] == 'SENT')
                   for e in status['events']):
                failures.append('unresolved_order')
            if side == 'BUY':
                if exit_pending:
                    failures.append('position_exit_pending')
                if status['kill_reason'] or not status['session_active']:
                    failures.append('buys_paused_or_killed')
                equity = status.get('equity')
                if not equity or equity.get('fresh') is not True:
                    failures.append('equity_unavailable_or_stale')
                else:
                    expires(equity['observed_at'], cfg['reconciliation_max_age_seconds'])
                    for mark in equity['marks'].values():
                        expires(mark['quoted_at'], cfg['quote_max_age_seconds'])
                if validation is None and not all(quality_gates(status['performance'], equity).values()):
                    failures.append('performance_gates')
                if status['open_positions'] >= cfg['max_open_positions']:
                    failures.append('max_open_positions')
            if event is not None:
                expected_state = 'SENT' if owned is not None else 'READY'
                if quote is None or event.get('side') != side or event.get('state') != expected_state:
                    failures.append('invalid_execution_intent')
                else:
                    if side == 'BUY' and age(event['signal_at'], now) > cfg['signal_max_age_seconds']:
                        failures.append('stale_signal')
                    if side == 'BUY':
                        expires(event['signal_at'], cfg['signal_max_age_seconds'])
                    if age(quote.get('quoted_at'), now) > cfg['quote_max_age_seconds']:
                        failures.append('stale_quote')
                    expires(quote['quoted_at'], cfg['quote_max_age_seconds'])
                    source = 'JUPITER_V2_ORDER_QUOTE_OBSERVATION' if validation else 'GMGN_ORDER_QUOTE'
                    if quote.get('event_id') != event['event_id'] or quote.get('source') != source:
                        failures.append('unbound_quote')
                    from mykonos_meme_scout.execution_rules import quote_failures
                    failures.extend(quote_failures(quote, cfg))
                    if quote.get('wallet') != status.get('wallet'):
                        failures.append('wallet_mismatch')
                    amount = raw(quote.get('input_raw'))
                    if amount <= 0 or raw(quote.get('minimum_output_raw')) <= 0:
                        failures.append('invalid_amount')
                    if side == 'SELL':
                        trade = next((t for t in status['trades'] if t['trade_id'] == event['trade_id']), None)
                        allowed_trade_states = {'SELL_SENT'} if owned is not None else {'POSITION_OPEN', 'EMERGENCY_EXIT'}
                        if (not trade or trade['wallet'] != quote.get('wallet') or amount != raw(trade['remaining_raw'])
                                or amount != raw(event.get('amount_raw')) or trade['state'] not in allowed_trade_states):
                            failures.append('sell_position_mismatch')
        except (StateError, OSError, ValueError, KeyError, TypeError):
            failures.append('state_or_evidence_unknown')
        valid_until = min(expiries).isoformat() if expiries else now
        monotonic_anchor = time.monotonic()
        current = self.clock()
        try:
            age(now, current)
            ttl = age(current, valid_until)
        except StateError:
            ttl = 0
            failures.append('evidence_expired_during_assessment')
        now = current
        monotonic_deadline = min([monotonic_anchor + ttl, *owned_deadlines])
        if owned_deadlines and time.monotonic() >= min(owned_deadlines):
            failures.append('heartbeat:POSITION_MANAGER')
        ready = not failures
        isolated = bool(validation and validation['domain'] == 'ISOLATED'
                        and getattr(self.store, '_isolated_transport', None) is not None and gate['value'] == 'OFF')
        if validation and not isolated and (not validation['started'] or not all(self.store.cfg[k] is True for k in
                ('signing', 'broadcast_enabled', 'live_trading_enabled'))):
            # Prepared/ready is distinct from live authority.
            gate = dict(gate, value='OFF')
        authorized = ready and (isolated or gate['value'] == 'ON' or close_only_authorized)
        # Do not call a missing adapter ARMED or a closed owner gate LIVE.
        mode = ('ISOLATED_VALIDATION' if authorized and isolated else
                'CLOSE_ONLY' if authorized and gate['value'] == 'OFF' else
                'AUTONOMOUS_LIVE' if authorized else 'ARMED_NO_BROADCAST' if ready
                else 'UNKNOWN')
        decision = {'observed_at': now, 'side': side, 'owner_gate': gate['value'],
                    'ready': ready, 'authorized': authorized, 'mode': mode,
                    'failures': sorted(set(failures)), 'trade_id': event.get('trade_id') if event else None}
        if validation:
            decision['evidence_domain'] = validation['domain']
        if ready:
            decision['guard_revision'] = revision
            decision['valid_until'] = valid_until
        if lease is not None and authorized:
            if not self._record_approval(lease, decision, revision, now, valid_until, monotonic_deadline):
                decision.update(ready=False, authorized=False, mode='UNKNOWN')
                decision['failures'].append('evidence_expired_before_approval')
        with self.store.transaction() as c:
            self.store.audit(c, 'risk_decision', decision, decision['trade_id'])
        # The audit transaction can wait too. No approval may outlive the
        # original owned lease while the caller is waiting for this decision.
        if ready and time.monotonic() >= monotonic_deadline:
            if lease is not None:
                self.revoke(lease)
            decision.update(ready=False, authorized=False, mode='UNKNOWN')
            decision['failures'].append('evidence_expired_before_return')
        return decision
