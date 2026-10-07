"""Preregistered, append-only PF2 champion governance; no wallet interface."""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .ledger import canonical
from .safety import assert_zero_authority
from .validation import TradeEvidence, evaluate_trades, evidence_errors, finite, paired_comparison


class GovernorRegistry:
    def __init__(self, directory, *, clock=time.time):
        self.directory=Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.clock=clock
        self.conn=sqlite3.connect(self.directory/'registry.db',timeout=10)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS registrations(
          model_hash TEXT PRIMARY KEY, artifact TEXT NOT NULL, frozen_at REAL NOT NULL,
          cohort_end REAL NOT NULL, excluded_coins TEXT NOT NULL, policy TEXT NOT NULL,
          authority_pct INTEGER NOT NULL CHECK(authority_pct=0));
        CREATE TABLE IF NOT EXISTS decisions(
          decision_id TEXT PRIMARY KEY, cohort_hash TEXT NOT NULL, model_hash TEXT NOT NULL, coin TEXT NOT NULL,
          decision_ts REAL NOT NULL, recorded_at REAL NOT NULL, payload TEXT NOT NULL,
          authority_pct INTEGER NOT NULL CHECK(authority_pct=0), UNIQUE(cohort_hash,model_hash,coin));
        CREATE TABLE IF NOT EXISTS outcomes(
          decision_id TEXT PRIMARY KEY, settled_at REAL NOT NULL, payload TEXT NOT NULL,
          authority_pct INTEGER NOT NULL CHECK(authority_pct=0));
        CREATE TABLE IF NOT EXISTS promotions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, model_hash TEXT NOT NULL,
          previous_hash TEXT, promoted_at REAL NOT NULL, report TEXT NOT NULL,
          authority_pct INTEGER NOT NULL CHECK(authority_pct=0));
        ''')
        for table in ('registrations','decisions','outcomes','promotions'):
            for op in ('UPDATE','DELETE'):
                self.conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_{op.lower()}_blocked
                BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'governance history is immutable'); END''')
        self.conn.commit()

    def close(self):
        self.conn.close()

    @property
    def champion(self):
        r=self.conn.execute('SELECT model_hash FROM promotions ORDER BY id DESC LIMIT 1').fetchone()
        return r[0] if r else None

    def register_challenger(self, model_path, training_coins=(), validation_coins=(), test_coins=(), historical_coins=()):
        content=Path(model_path).read_bytes()
        payload=json.loads(content)
        assert_zero_authority(payload)
        from .model import SequenceBrain
        model=SequenceBrain.load(model_path)
        if model.training_source_domains - {"REAL"}:
            raise ValueError("Only observed-market lineage may enter the real forward governor")
        sha=model.model_sha256
        excluded=sorted(set(training_coins)|set(validation_coins)|set(test_coins)|set(historical_coins)|set(model.training_coins))
        existing=self.conn.execute('SELECT * FROM registrations WHERE model_hash=?',(sha,)).fetchone()
        if existing:
            if not set(excluded)<=set(json.loads(existing['excluded_coins'])):
                raise ValueError('Cannot change exclusion set after freeze')
            return sha
        artifact=self.directory/(sha+'.json')
        if artifact.exists() and artifact.read_bytes()!=content:
            raise ValueError('Immutable artifact conflict')
        if not artifact.exists():
            with artifact.open('xb') as f:
                f.write(content)
        frozen=self.clock()
        policy={'baseline':468,'min_explorer_id':469,'min_coins':50,'min_losses':5,
                'pf_floor':2.0,'cohort_seconds':86400,'horizon_seconds':120,
                'cost_stress_multiplier':2.0,'max_recording_lag_seconds':5,
                'max_drawdown_pct':10.0,'minimum_regime_coins':10,
                'execution_authority_pct':0}
        self.conn.execute('INSERT INTO registrations VALUES (?,?,?,?,?,?,0)',
            (sha,artifact.name,frozen,frozen+86400,canonical(excluded),canonical(policy)))
        self.conn.commit()
        return sha

    def registration(self, sha):
        row=self.conn.execute('SELECT * FROM registrations WHERE model_hash=?',(sha,)).fetchone()
        if not row:
            raise ValueError('Unregistered model')
        result=dict(row)
        artifact=Path(result['artifact'])
        if not artifact.is_absolute():
            result['artifact']=str(self.directory/artifact)
        from .model import SequenceBrain
        if SequenceBrain.load(result['artifact']).model_sha256!=sha:
            raise ValueError('Frozen model hash changed')
        return result

    def record_decision(self, row: TradeEvidence, *, cohort_hash=None):
        self.registration(row.model_hash)
        cohort_hash=cohort_hash or row.model_hash
        registration=self.registration(cohort_hash)
        now=self.clock()
        if row.outcome_ts is not None or row.gross_return_pct is not None:
            raise ValueError('Decision must precede outcomes')
        if not finite(row.decision_ts) or not registration['frozen_at']<=row.decision_ts<registration['cohort_end']:
            raise ValueError('Decision outside frozen cohort')
        if not 0<=now-row.decision_ts<=5:
            raise ValueError('Decision was not recorded contemporaneously')
        if row.source!='REAL' or type(row.explorer_id) is not int or row.explorer_id<469:
            raise ValueError('Requires observed forward Explorer ID >=469')
        if row.coin in set(json.loads(registration['excluded_coins'])):
            raise ValueError('Coin previously seen during research or training')
        if not finite(row.round_trip_cost_pct) or row.round_trip_cost_pct<=0 or row.action not in ('TRADE','ABSTAIN','VETO'):
            raise ValueError('Invalid decision or unknown costs')
        payload={**asdict(row),'recorded_at':now}
        identity=hashlib.sha256(canonical([cohort_hash,row.model_hash,row.coin]).encode()).hexdigest()
        old=self.conn.execute('SELECT payload FROM decisions WHERE decision_id=?',(identity,)).fetchone()
        if old:
            return identity
        self.conn.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,0)',
            (identity,cohort_hash,row.model_hash,row.coin,row.decision_ts,now,canonical(payload)))
        self.conn.commit()
        return identity

    def settle_decision(self, decision_id, *, outcome_ts, gross_return_pct):
        row=self.conn.execute('SELECT * FROM decisions WHERE decision_id=?',(decision_id,)).fetchone()
        if not row:
            raise ValueError('Decision not preregistered')
        now=self.clock()
        if not finite(outcome_ts) or not 120<=outcome_ts-row['decision_ts']<=121 or outcome_ts>now:
            raise ValueError('Requires observed 120-second endpoint within one second')
        payload={**json.loads(row['payload']),'outcome_ts':outcome_ts,'gross_return_pct':gross_return_pct}
        errors=evidence_errors(TradeEvidence(**payload))
        if errors:
            raise ValueError(','.join(errors))
        old=self.conn.execute('SELECT payload FROM outcomes WHERE decision_id=?',(decision_id,)).fetchone()
        if old:
            if old['payload']!=canonical(payload):
                raise ValueError('Outcome cannot change')
            return
        self.conn.execute('INSERT INTO outcomes VALUES (?,?,?,0)',(decision_id,now,canonical(payload)))
        self.conn.commit()

    def evidence(self, sha, *, cohort_hash=None):
        return [TradeEvidence(**json.loads(r['payload'])) for r in self.conn.execute('''
          SELECT o.payload FROM outcomes o JOIN decisions d USING(decision_id)
          WHERE d.model_hash=? AND d.cohort_hash=? ORDER BY d.decision_ts,d.decision_id''',(sha,cohort_hash or sha))]

    def assess(self, sha):
        registration=self.registration(sha)
        evidence=self.evidence(sha)
        report=evaluate_trades(evidence)
        reasons=[]
        pending=self.conn.execute('''SELECT COUNT(*) FROM decisions d LEFT JOIN outcomes o USING(decision_id)
          WHERE d.cohort_hash=? AND o.decision_id IS NULL''',(sha,)).fetchone()[0]
        if self.clock()<registration['cohort_end']+121:
            reasons.append('preregistered_24h_cohort_not_complete')
        if pending:
            reasons.append('unresolved_decisions_cannot_be_dropped')
        if not report['complete'] or report['traded_coins']<50 or report['losses']<5:
            reasons.append('insufficient_independent_forward_trades_or_losses')
        for key in ('oos_pf_after_cost','cost_stress_pf','top_1_removal_pf','top_3_removal_pf','bootstrap_pf_p10'):
            if not finite(report.get(key)) or report[key]<2:
                reasons.append(key+'_below_2_or_undefined')
        if not finite(report.get('expectancy_pct')) or report['expectancy_pct']<=0:
            reasons.append('nonpositive_expectancy')
        if not finite(report.get('max_drawdown_pct')) or report['max_drawdown_pct']>10:
            reasons.append('drawdown_limit')
        if not finite(report.get('tail_loss_cvar_5_pct')) or report['tail_loss_cvar_5_pct']<-30:
            reasons.append('tail_loss_limit')
        if report['calibration_count']<50 or report['brier_score'] is None or report['brier_score']>.25 or report['calibration_ece']>.20:
            reasons.append('calibration_not_confirmed')
        regimes=[v for k,v in report['regimes'].items() if k!='unknown' and v['traded_coins']>=10]
        if len(regimes)<2 or any(not finite(v['profit_factor']) or v['profit_factor']<2 for v in regimes):
            reasons.append('regime_stability_unconfirmed')
        if any(v['traded_coins']<10 or not finite(v['profit_factor']) or v['profit_factor']<2 for v in report['chronological_forward_blocks']):
            reasons.append('chronological_stability_unconfirmed')
        champion=self.champion
        if champion and champion!=sha:
            # Historical champion evidence cannot be used against a fresh cohort.
            pair=paired_comparison(evidence,self.evidence(champion,cohort_hash=sha))
            report['champion_comparison']=pair
            if not pair['eligible'] or pair.get('paired_bootstrap_improvement_p10',0)<=0:
                reasons.append('paired_champion_improvement_unproven')
            old=evaluate_trades(self.evidence(champion,cohort_hash=sha))
            for name,metrics in old['specialists'].items():
                if finite(metrics['profit_factor']) and metrics['profit_factor']>=2:
                    new=report['specialists'].get(name,{})
                    if not finite(new.get('profit_factor')) or new['profit_factor']<metrics['profit_factor']:
                        reasons.append('profitable_specialist_regression:'+name)
        return {'eligible':not reasons,'reasons':reasons,'model_hash':sha,'champion':champion,
                'pending':pending,'metrics':report,'execution_authority_pct':0}

    def promote(self, sha):
        # Serialized compare-and-swap; a concurrent promotion cannot overwrite a
        # champion checked outside this transaction.
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            report=self.assess(sha)
            if not report['eligible']:
                self.conn.rollback()
                return report
            if self.champion!=sha:
                self.conn.execute('INSERT INTO promotions(model_hash,previous_hash,promoted_at,report,authority_pct) VALUES (?,?,?,?,0)',
                    (sha,self.champion,self.clock(),canonical(report)))
            self.conn.commit()
            return report
        except Exception:
            self.conn.rollback()
            raise
