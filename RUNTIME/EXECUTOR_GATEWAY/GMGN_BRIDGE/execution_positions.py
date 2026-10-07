"""Owned position decisions on durable evidence; never quotes, signs or sends.

V1 freezes the existing paper exit parameters. Wallet cash includes historical
fees/rent; only future exit costs are subtracted from executable liquidation.
"""
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from execution_state import StateError, age, identity, raw
from mykonos_meme_scout.projection_io import write_json_projection

POLICY = MappingProxyType({'version': 1, 'maximum_hold_seconds': 180,
    'hard_stop_pct': 12, 'take_profit_pct': 20, 'trailing_arm_pct': 10, 'trailing_stop_pct': 6})
POLICY_HASH = identity('position-policy', dict(POLICY))
BLOCK_ONLY = frozenset({'restart_with_execution_obligation', 'execution_timeout',
    'partial_execution', 'unresolved_executor_order', 'execution_failure',
    'execution_unknown_execution', 'execution_rejected', 'execution_deferred',
    'deferred_after_send', 'unverified_fill'})


def require_policy(store, c):
    record = store.meta(c, 'position_policy')
    if (not isinstance(record, dict) or set(record) != {'policy', 'hash', 'frozen_at'}
            or record['policy'] != dict(POLICY) or record['hash'] != POLICY_HASH
            or identity('position-policy', record['policy']) != POLICY_HASH):
        raise StateError('position_policy_unknown')
    age(record['frozen_at'], store.clock())
    rows = c.execute("SELECT at,doc FROM audit WHERE action='position_policy'").fetchall()
    if len(rows) != 1 or json.loads(rows[0]['doc']) != record:
        raise StateError('position_policy_audit_missing')
    age(record['frozen_at'], rows[0]['at'])
    age(rows[0]['at'], store.clock())
    return record


def initialize_policy(store, c):
    if store.meta(c, 'position_policy') is not None:
        return require_policy(store, c)
    if c.execute("SELECT 1 FROM audit WHERE action='position_policy' LIMIT 1").fetchone():
        raise StateError('position_policy_lost')
    trades = {t['trade_id']: t for t in store.all(c, 'trades')}
    if (any(raw(t['remaining_raw']) for t in trades.values()) or any(
            e['side'] == 'BUY' and e.get('sent_at') and trades[e['trade_id']]['state'] != 'CLOSED'
            for e in store.all(c, 'events'))):
        raise StateError('position_policy_requires_unexecuted_ledger')
    record = {'policy': dict(POLICY), 'hash': POLICY_HASH, 'frozen_at': store.clock()}
    store.set_meta(c, 'position_policy', record)
    store.audit(c, 'position_policy', record)
    return record


def bind_trade(store, c, trade):
    require_policy(store, c)
    key = 'position_track:' + trade['trade_id']
    if (store.meta(c, key) is not None or c.execute(
            "SELECT 1 FROM audit WHERE action='position_binding' AND trade_id=? LIMIT 1",
            (trade['trade_id'],)).fetchone() or any(
            e['trade_id'] == trade['trade_id'] and e['side'] == 'BUY' and e.get('sent_at')
            for e in store.all(c, 'events'))):
        raise StateError('position_binding_already_exists')
    tracking = {'trade_id': trade['trade_id'], 'mint': trade['mint'], 'policy_hash': POLICY_HASH,
        'bound_at': store.clock(), 'mark_count': 0, 'peak_lamports': None,
        'invested_lamports': None, 'last_mark': None}
    store.set_meta(c, key, tracking)
    store.audit(c, 'position_binding', tracking, trade['trade_id'])


def require_tracking(store, c, trade):
    tracking = store.meta(c, 'position_track:' + trade['trade_id'])
    fields = {'trade_id', 'mint', 'policy_hash', 'bound_at', 'mark_count',
              'peak_lamports', 'invested_lamports', 'last_mark'}
    if (not isinstance(tracking, dict) or set(tracking) != fields
            or tracking['trade_id'] != trade['trade_id'] or tracking['mint'] != trade['mint']
            or tracking['policy_hash'] != POLICY_HASH or type(tracking['mark_count']) is not int
            or tracking['mark_count'] < 0):
        raise StateError('position_tracking_unknown')
    age(tracking['bound_at'], store.clock())
    initial = dict(tracking, mark_count=0, peak_lamports=None, invested_lamports=None, last_mark=None)
    rows = c.execute("SELECT at,doc FROM audit WHERE action='position_binding' AND trade_id=?",
                     (trade['trade_id'],)).fetchall()
    if len(rows) != 1 or json.loads(rows[0]['doc']) != initial:
        raise StateError('position_binding_audit_missing')
    age(tracking['bound_at'], rows[0]['at'])
    age(rows[0]['at'], store.clock())
    marked = c.execute("SELECT doc FROM audit WHERE action='position_mark' AND trade_id=? ORDER BY id DESC LIMIT 1",
                       (trade['trade_id'],)).fetchone()
    if tracking['mark_count'] == 0:
        if tracking != initial or marked:
            raise StateError('position_peak_lost')
    elif (not marked or json.loads(marked[0]).get('tracking') != tracking
          or raw(tracking['peak_lamports']) < 0 or raw(tracking['invested_lamports']) <= 0
          or not isinstance(tracking['last_mark'], dict)):
        raise StateError('position_peak_lost')
    return tracking


def capture_risk_exits(store, c, cause):
    if cause in BLOCK_ONLY:
        return
    try:
        trades = store.all(c, 'trades')
    except (StateError, ValueError, KeyError, TypeError):
        # A corruption alarm must remain durable even if individual positions
        # cannot be safely identified. Never invent or replace an inventory.
        store.audit(c, 'position_risk_capture_unknown', {'cause': cause})
        return
    for trade in trades:
        if not raw(trade['remaining_raw']):
            continue
        key = 'position_risk_exit:' + trade['trade_id']
        try:
            existing = require_risk_trigger(store, c, trade)
        except (StateError, ValueError, KeyError, TypeError):
            store.audit(c, 'position_risk_capture_unknown', {'cause': cause}, trade['trade_id'])
            continue
        if existing is not None:
            continue
        trigger = {'trade_id': trade['trade_id'], 'mint': trade['mint'], 'wallet': trade['wallet'],
                   'at': store.clock(), 'cause': cause}
        store.set_meta(c, key, trigger)
        store.audit(c, 'position_risk_trigger', trigger, trade['trade_id'])


def require_risk_trigger(store, c, trade):
    trigger = store.meta(c, 'position_risk_exit:' + trade['trade_id'])
    rows = c.execute("SELECT at,doc FROM audit WHERE action='position_risk_trigger' AND trade_id=?",
                     (trade['trade_id'],)).fetchall()
    if trigger is None:
        if rows:
            raise StateError('position_risk_trigger_lost')
        return None
    if (not isinstance(trigger, dict) or set(trigger) != {'trade_id', 'mint', 'wallet', 'at', 'cause'}
            or any(trigger[k] != trade[k] for k in ('trade_id', 'mint', 'wallet'))
            or not isinstance(trigger['cause'], str) or not trigger['cause']
            or trigger['cause'] in BLOCK_ONLY):
        raise StateError('position_risk_trigger_unknown')
    age(trigger['at'], store.clock())
    if len(rows) != 1 or json.loads(rows[0]['doc']) != trigger:
        raise StateError('position_risk_trigger_audit_missing')
    age(trigger['at'], rows[0]['at'])
    age(rows[0]['at'], store.clock())
    return trigger


def buy_exit_pending(store, c):
    return any(raw(t['remaining_raw']) and (t['exit_requested'] or
               require_risk_trigger(store, c, t) is not None)
               for t in store.all(c, 'trades'))


def recoveries(store, c):
    records = {r[0].removeprefix('position_recovery:'): json.loads(r[1]) for r in
               c.execute("SELECT key,value FROM meta WHERE key GLOB 'position_recovery:*'")}
    audited = {}
    for row in c.execute("SELECT at,doc FROM audit WHERE action='position_recovery'"):
        body = json.loads(row['doc'])
        key = body.get('event_id') if isinstance(body, dict) else None
        if key in audited:
            raise StateError('position_recovery_audit_duplicate')
        audited[key] = (row['at'], body)
    if set(records) != set(audited):
        raise StateError('position_recovery_obligation_lost')
    for key, record in records.items():
        event = store.get(c, 'events', key)
        fields = {'trade_id', 'event_id', 'request_id', 'wallet', 'mint', 'side', 'requested_at'}
        if (not event or not isinstance(record, dict) or set(record) != fields
                or any(record[k] != event.get(k) for k in fields - {'requested_at'})
                or not event.get('sent_at') or not event.get('request_id')
                or audited[key][1] != record):
            raise StateError('position_recovery_obligation_unknown')
        age(record['requested_at'], audited[key][0])
        age(audited[key][0], store.clock())
    return tuple(records.values())


def capture_recovery(store, c, event):
    existing = recoveries(store, c)
    if any(r['event_id'] == event['event_id'] for r in existing):
        return
    record = {k: event[k] for k in ('trade_id', 'event_id', 'request_id', 'wallet', 'mint', 'side')}
    record['requested_at'] = store.clock()
    store.set_meta(c, 'position_recovery:' + event['event_id'], record)
    store.audit(c, 'position_recovery', record, event['trade_id'])


class PositionManager:
    def __init__(self, store, status_path=None):
        store.pin_config()
        self.store = store
        self.status_path = Path(status_path) if status_path is not None else None
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread = None
        self._quote_leases = {}
        self._tick_anchor = None
        self.state = {'component': 'POSITION_MANAGER', 'status': 'UNKNOWN', 'observed_at': None,
            'source': str(store.path) + ':receipts/position_policy/position_track',
            'valuation_state': 'UNKNOWN', 'positions': 0, 'exit_intents': 0,
            'policy_hash': POLICY_HASH, 'last_error': None, 'broadcast': False,
            'valid_until_monotonic': 0}
        with store.transaction(deadline=time.monotonic() + 2) as c:
            initialize_policy(store, c)

    def _inventory(self, c, trade, now):
        event_ids = {e['event_id'] for e in self.store.all(c, 'events')
                     if e['trade_id'] == trade['trade_id'] and e['side'] == 'BUY'}
        proofs = [r['proof'] for r in self.store.all(c, 'receipts') if r['event_id'] in event_ids]
        if not proofs or any(p.get('commitment') != 'finalized' or not p.get('source')
                             or raw(p['wallet_delta_lamports'], signed=True) >= 0 for p in proofs):
            raise StateError('position_buy_receipts_unknown')
        for proof in proofs:
            age(proof['block_time'], now)
        opened = min(proofs, key=lambda p: datetime.fromisoformat(p['block_time'].replace('Z', '+00:00')))['block_time']
        invested = -sum(raw(p['wallet_delta_lamports'], signed=True) for p in proofs)
        return opened, invested

    def _mark(self, c, trade):
        started, checked_at = time.monotonic(), self.store.clock()
        equity = self.store.equity_status(c)
        if not equity or not equity['fresh']:
            return None, None
        mark = equity['marks'].get(trade['trade_id'])
        from execution_validation import require as validation_contract
        source = 'JUPITER_V2_ORDER_QUOTE_OBSERVATION' if validation_contract(self.store, c) else 'GMGN_ORDER_QUOTE'
        if (not isinstance(mark, dict) or mark.get('source') != source
                or mark.get('wallet') != trade['wallet'] or mark.get('mint') != trade['mint']
                or raw(mark.get('remaining_raw')) != raw(trade['remaining_raw'])):
            raise StateError('position_mark_identity_unknown')
        minimum, costs = raw(mark.get('minimum_output_lamports')), raw(mark.get('exit_cost_budget_lamports'))
        if minimum <= 0:
            raise StateError('position_mark_output_unknown')
        marker = {'reconciliation_revision': equity['reconciliation_revision'],
            'equity_observed_at': equity['observed_at'], 'quoted_at': mark['quoted_at'],
            'wallet': trade['wallet'], 'mint': trade['mint'], 'remaining_raw': trade['remaining_raw'],
            'minimum_output_lamports': str(minimum), 'exit_cost_budget_lamports': str(costs),
            'wallet_cash_lamports': trade['wallet_delta_lamports']}
        times = [(mark['quoted_at'], self.store.cfg['quote_max_age_seconds']),
                 (equity['observed_at'], self.store.cfg['reconciliation_max_age_seconds']),
                 (self.store.meta(c, 'reconciled_at'), self.store.cfg['reconciliation_max_age_seconds'])]
        budget = started + min(limit-age(at, checked_at) for at, limit in times)
        if self._tick_anchor is not None:
            for at, limit in times:
                try:
                    previous_age = age(at, self._tick_anchor[1])
                except StateError:
                    # Already validated against checked_at above: an actual new
                    # observation may have arrived between the two transactions.
                    continue
                budget = min(budget, self._tick_anchor[0]+limit-previous_age)
        key = identity('position-quote', {k: v for k, v in marker.items()
            if k not in {'reconciliation_revision', 'equity_observed_at', 'wallet_cash_lamports'}})
        old = self._quote_leases.get(trade['trade_id'])
        if old is not None:
            age(old[1], mark['quoted_at'])
            if old[0] == key:
                budget = min(budget, old[2])
        # Repeated ticks and COMMIT callbacks cannot renew one quote's original
        # monotone budget, even if wall time stalls. Expired leases stay expired.
        self._quote_leases[trade['trade_id']] = (key, mark['quoted_at'], budget)
        return (marker, budget) if time.monotonic() < budget else (None, None)

    def tick(self):
        if not self._lock.acquire(blocking=False):
            raise StateError('position_manager_busy')
        tick_started = time.monotonic()
        deadline = tick_started + 2
        first_wall = self.store.clock()
        self._tick_anchor = (tick_started, first_wall)
        last_wall = first_wall
        def fresh():
            nonlocal last_wall
            now = self.store.clock()
            age(last_wall, now)
            last_wall = now
            if time.monotonic() >= deadline:
                raise StateError('position_manager_deadline')
        try:
            materialize, marks_verified, count, price_budgets = [], 0, 0, []
            # Non-price obligations commit independently. A later quote/peak
            # deadline cannot roll back a maximum-hold or persistent risk exit.
            with self.store.transaction(deadline=deadline, before_commit=fresh) as c:
                fresh()
                require_policy(self.store, c)
                self.store.validate_links(c)
                self.store.expire(c)
                previous = self.store.meta(c, 'position_manager_at')
                if previous is not None:
                    age(previous, self.store.clock())
                for trade in self.store.all(c, 'trades'):
                    fresh()
                    if not raw(trade['remaining_raw']):
                        continue
                    require_tracking(self.store, c, trade)
                    opened, _ = self._inventory(c, trade, self.store.clock())
                    reason = 'risk_exit' if require_risk_trigger(self.store, c, trade) else None
                    if not reason and any(r['trade_id'] == trade['trade_id'] for r in recoveries(self.store, c)):
                        reason = 'recovery_exit'
                    if not reason and age(opened, self.store.clock()) >= POLICY['maximum_hold_seconds']:
                        reason = 'maximum_hold'
                    if reason:
                        self.store.request_exit(c, trade, reason)
                fresh()
            with self.store.transaction(deadline=deadline, before_commit=fresh) as c:
                fresh()
                require_policy(self.store, c)
                self.store.validate_links(c)
                self.store.expire(c)
                previous = self.store.meta(c, 'position_manager_at')
                if previous is not None:
                    age(previous, self.store.clock())
                for trade in self.store.all(c, 'trades'):
                    fresh()
                    if not raw(trade['remaining_raw']):
                        continue
                    count += 1
                    tracking = require_tracking(self.store, c, trade)
                    _, invested = self._inventory(c, trade, self.store.clock())
                    mark, mark_deadline = self._mark(c, trade)
                    if mark is not None:
                        marks_verified += 1
                        cash = raw(trade['wallet_delta_lamports'], signed=True)
                        liquidation = raw(mark['minimum_output_lamports']) - raw(mark['exit_cost_budget_lamports'])
                        pnl, value = cash + liquidation, invested + cash + liquidation
                        peak = max(0, value, raw(tracking['peak_lamports']) if tracking['mark_count'] else 0)
                        if tracking['mark_count'] and raw(tracking['invested_lamports']) != invested:
                            raise StateError('position_investment_changed')
                        if tracking['last_mark'] != mark:
                            tracking.update(mark_count=tracking['mark_count'] + 1, peak_lamports=str(peak),
                                            invested_lamports=str(invested), last_mark=mark)
                            self.store.set_meta(c, 'position_track:' + trade['trade_id'], tracking)
                            self.store.audit(c, 'position_mark', {'tracking': tracking, 'net_pnl_lamports': str(pnl),
                                             'return_lamports': str(value)}, trade['trade_id'])
                        from mykonos_meme_scout.execution_rules import price_exit
                        price_reason = price_exit(invested, value, peak, POLICY)
                        if price_reason:
                            self.store.request_exit(c, trade, price_reason)
                        # Preserve this exact original observation, deadline and
                        # quantity until COMMIT. Never refresh an expired mark.
                        def mark_fresh(tid=trade['trade_id'], original=mark, original_deadline=mark_deadline):
                            fresh()
                            current = self.store.get(c, 'trades', tid)
                            if time.monotonic() >= original_deadline or self._mark(c, current)[0] != original:
                                raise StateError('position_mark_changed_or_expired')
                            fresh()
                            if time.monotonic() >= original_deadline:
                                raise StateError('position_mark_changed_or_expired')
                        c.commit_guards.append(mark_fresh)
                        price_budgets.extend([
                            (mark['quoted_at'], self.store.cfg['quote_max_age_seconds'], mark_deadline),
                            (mark['equity_observed_at'], self.store.cfg['reconciliation_max_age_seconds'], mark_deadline),
                            (self.store.meta(c, 'reconciled_at'), self.store.cfg['reconciliation_max_age_seconds'], mark_deadline)])
                    if trade['exit_requested']:
                        materialize.append(trade['trade_id'])
                self.store.set_meta(c, 'position_manager_at', self.store.clock())
                fresh()
                active_ids = {t['trade_id'] for t in self.store.all(c, 'trades') if raw(t['remaining_raw'])}
                # This cheap final guard runs after EVERY expensive per-position
                # guard, immediately before each COMMIT attempt. Guard B cannot
                # spend the remaining evidence budget of a previously checked A.
                def all_price_times_fresh():
                    nonlocal last_wall
                    fresh()
                    wall, mono = self.store.clock(), time.monotonic()
                    age(last_wall, wall)
                    last_wall = wall
                    if mono >= deadline:
                        raise StateError('position_manager_deadline')
                    for quoted_at, limit, original_deadline in price_budgets:
                        if age(quoted_at, wall) > limit or mono >= original_deadline:
                            raise StateError('position_mark_changed_or_expired')
                c.commit_guards.append(all_price_times_fresh)
            # The committed intent survives a stale/unknown inventory or a
            # failed subsequent materialization. This never submits an order.
            for tid in materialize:
                fresh()
                self.store.materialize_exit(tid, deadline=deadline)
            fresh()
            self._quote_leases = {tid: lease for tid, lease in self._quote_leases.items() if tid in active_ids}
            self.state.update(status='PASS', observed_at=self.store.clock(), positions=count,
                exit_intents=len(materialize), last_error=None,
                valid_until_monotonic=deadline,
                valuation_state='VERIFIED' if count == marks_verified else 'UNKNOWN')
            self._publish(deadline)
            return dict(self.state)
        except Exception as error:
            self.state.update(status='UNKNOWN', valuation_state='UNKNOWN', last_error=type(error).__name__,
                              observed_at=self.store.clock(), valid_until_monotonic=0)
            try:
                self._publish(deadline)
            except Exception:
                pass
            raise
        finally:
            self._tick_anchor = None
            self._lock.release()

    def _publish(self, deadline):
        if self.status_path is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StateError('position_manager_deadline')
            write_json_projection(self.status_path, self.state, retry_seconds=min(.1, remaining))
            if time.monotonic() >= deadline:
                raise StateError('position_manager_deadline')

    def start(self):
        if self._thread is not None:
            raise StateError('position_manager_already_started')
        self._thread = threading.Thread(target=self._run, name='imc-position-manager', daemon=True)
        self._thread.start()

    def require_current(self):
        # Embedded callers retain the synchronous path. Once the gateway owns
        # a loop, polling consumes its evidence instead of running a second tick.
        if self._thread is None:
            return self.tick()
        result = self.heartbeat(wait_seconds=.5)
        if result['status'] != 'PASS':
            raise StateError('position_manager_busy' if result['reason'] == 'busy'
                             else 'position_manager_health_unknown')
        return result

    def heartbeat(self, *, wait_seconds=0):
        """Trusted in-process probe; a cached projection alone is not health.

        The owning gateway must use this probe when assessing real execution.
        A dead/busy/stopped loop or late publication cannot supply a fresh PASS.
        """
        if (type(wait_seconds) not in (int, float) or not math.isfinite(wait_seconds)
                or not 0 <= wait_seconds <= .5):
            raise StateError('position_manager_probe_wait_invalid')
        if not self._lock.acquire(timeout=wait_seconds):
            # Never copy an in-flight state without the owning lock.
            return {'status': 'UNKNOWN', 'reason': 'busy', 'observed_at': None, 'last_error': None}
        try:
            result = dict(self.state)
            reason = ('not_started' if self._thread is None else
                      'dead' if not self._thread.is_alive() else
                      'stopped' if self._stop.is_set() else
                      'tick_failed' if result['status'] != 'PASS' else
                      'expired' if time.monotonic() >= result['valid_until_monotonic'] else None)
            if reason is not None:
                result['status'] = 'UNKNOWN'
            result['reason'] = reason
            return result
        finally:
            self._lock.release()

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:
                pass  # durable obligations survive; published health is UNKNOWN
            self._stop.wait(max(.1, .5-(time.monotonic()-started)))

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                self.state.update(status='UNKNOWN', last_error='PositionManagerShutdownTimeout')
                raise StateError('position_manager_shutdown_timeout')
