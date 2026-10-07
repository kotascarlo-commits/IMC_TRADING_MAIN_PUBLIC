"""New append-only sidecar. Never migrates or writes historical databases."""
from __future__ import annotations
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class BrainLedger:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS brain_metadata(
          key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS brain_decisions(
          decision_id TEXT PRIMARY KEY, coin_id TEXT NOT NULL, observed_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL, model_hash TEXT NOT NULL,
          action TEXT NOT NULL CHECK(action IN ('TRADE','ABSTAIN','VETO')),
          source_domain TEXT NOT NULL, explorer_id INTEGER, payload TEXT NOT NULL,
          execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0));
        CREATE TABLE IF NOT EXISTS brain_outcomes(
          decision_id TEXT PRIMARY KEY REFERENCES brain_decisions(decision_id),
          settled_at TEXT NOT NULL, outcome_at TEXT NOT NULL, status TEXT NOT NULL,
          classification TEXT NOT NULL, payload TEXT NOT NULL,
          execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0));
        CREATE TABLE IF NOT EXISTS brain_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          kind TEXT NOT NULL, payload TEXT NOT NULL,
          execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0));
        CREATE INDEX IF NOT EXISTS brain_pending_time ON brain_decisions(observed_at);
        ''')
        for table in ('brain_metadata', 'brain_decisions', 'brain_outcomes', 'brain_events'):
            for operation in ('UPDATE', 'DELETE'):
                self.conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()}_blocked
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'brain evidence is immutable'); END''')
        self.conn.commit()

    def close(self):
        self.conn.close()

    def freeze_metadata(self, key, value):
        encoded = canonical(value)
        old = self.conn.execute('SELECT value FROM brain_metadata WHERE key=?', (key,)).fetchone()
        if old:
            if old['value'] != encoded:
                raise ValueError('Frozen metadata cannot change: ' + key)
            return
        self.conn.execute('INSERT INTO brain_metadata VALUES (?,?)', (key, encoded))
        self.conn.commit()

    def metadata(self, key, default=None):
        row = self.conn.execute('SELECT value FROM brain_metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def decide(self, record, prediction, action, model_hash, *, recorded_at=None):
        if action not in ('TRADE', 'ABSTAIN', 'VETO') or prediction.get('authority_pct') != 0:
            raise ValueError('Invalid decision or nonzero/missing model authority')
        observed = record.get('observed_at', record.get('timestamp'))
        recorded_at = recorded_at or utc_now()
        if datetime.fromisoformat(observed) > datetime.fromisoformat(recorded_at):
            raise ValueError('Future observations cannot be used')
        payload = {'record':record, 'prediction':prediction}
        identity = canonical([record['coin_id'], observed, model_hash, record.get('source_domain')])
        decision_id = hashlib.sha256(identity.encode()).hexdigest()
        self.conn.execute('INSERT OR IGNORE INTO brain_decisions VALUES (?,?,?,?,?,?,?,?,?,0)',
                          (decision_id, record['coin_id'], observed, recorded_at, model_hash, action,
                           record.get('source_domain','REAL'), record.get('explorer_id'), canonical(payload)))
        self.conn.commit()
        return decision_id

    def settle(self, decision_id, outcome_at, payload, *, status='COMPLETE', settled_at=None):
        decision = self.conn.execute('SELECT * FROM brain_decisions WHERE decision_id=?', (decision_id,)).fetchone()
        if decision is None:
            raise ValueError('No preregistered decision')
        settled_at = settled_at or utc_now()
        if not (datetime.fromisoformat(decision['observed_at']) < datetime.fromisoformat(outcome_at)
                <= datetime.fromisoformat(settled_at)):
            raise ValueError('Invalid outcome chronology')
        if status not in ('COMPLETE', 'CENSORED'):
            raise ValueError('Invalid settlement status')
        reward = payload.get('net_return_pct')
        if status == 'COMPLETE' and (reward is None or not math.isfinite(reward)):
            raise ValueError('Complete outcome requires finite after-cost return')
        classification = 'UNRESOLVED_DATA' if status == 'CENSORED' else (
            ('TRUE_POSITIVE' if reward > 0 else 'FALSE_POSITIVE') if decision['action'] == 'TRADE'
            else ('FALSE_NEGATIVE_MISSED_WINNER' if reward > 0 else 'TRUE_NEGATIVE_PREVENTED_LOSS'))
        self.conn.execute('INSERT OR IGNORE INTO brain_outcomes VALUES (?,?,?,?,?,?,0)',
                          (decision_id, settled_at, outcome_at, status, classification, canonical(payload)))
        self.conn.commit()

    def event(self, kind, payload):
        self.conn.execute('INSERT INTO brain_events(created_at,kind,payload,execution_authority_pct) VALUES (?,?,?,0)',
                          (utc_now(), kind, canonical(payload)))
        self.conn.commit()

    def pending(self):
        return [dict(row) for row in self.conn.execute('''SELECT d.* FROM brain_decisions d
          LEFT JOIN brain_outcomes o USING(decision_id) WHERE o.decision_id IS NULL ORDER BY observed_at''')]

    def status(self):
        return {'execution_authority_pct':0,
                'decisions':dict(self.conn.execute('SELECT action,COUNT(*) FROM brain_decisions GROUP BY action')),
                'outcomes':dict(self.conn.execute('SELECT classification,COUNT(*) FROM brain_outcomes GROUP BY classification')),
                'pending':self.conn.execute('''SELECT COUNT(*) FROM brain_decisions d LEFT JOIN brain_outcomes o
                  USING(decision_id) WHERE o.decision_id IS NULL''').fetchone()[0]}
