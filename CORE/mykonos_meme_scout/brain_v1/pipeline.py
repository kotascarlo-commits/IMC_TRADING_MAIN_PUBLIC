"""Reproducible training and research evaluation; test never selects a model."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np

from . import VERSION
from .costs import CostPolicy, net_return
from .data import FEATURE_NAMES, prepare_dataset, timestamp, open_readonly
from .governor import GovernorRegistry
from .ledger import BrainLedger, canonical, utc_now
from .model import SequenceBrain
from .safety import SafetyState, hard_shadow_decision
from .validation import TradeEvidence, evaluate_trades


def save_json(path, value, *, immutable=False):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    content=json.dumps(value,indent=2,sort_keys=True,allow_nan=False)
    if immutable:
        with path.open('x',encoding='utf-8') as f:
            f.write(content)
    else:
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(content,encoding='utf-8')
        temp.replace(path)


def policy_decision(prediction, observation, *, open_positions=0, exposure_pct=0.0,
                    daily_loss_pct=0.0, kill_switch=False):
    meta=prediction['meta']
    edge=meta.get('net_edge_pct')
    uncertainty=meta.get('uncertainty')
    tail=meta.get('tail_risk')
    if any(v is None or not math.isfinite(v) for v in (edge,uncertainty,tail)):
        proposed='ABSTAIN'
    elif tail>=.25:
        proposed='VETO'
    else:
        proposed='TRADE' if edge>max(1.0,2*uncertainty) else 'ABSTAIN'
    state=SafetyState(security_ok=observation.get('security_ok') is True,
        catastrophic_risk=bool(observation.get('hard_risk_seen')),
        provider_healthy=observation.get('feed_complete') is True and not observation.get('market_data_stale',True),
        liquidity_usd=observation.get('liquidity_usd'),exposure_pct=exposure_pct,
        open_positions=open_positions,daily_loss_pct=daily_loss_pct,kill_switch=kill_switch)
    result=hard_shadow_decision(proposed,state)
    return {**result,'proposed_action':proposed,'net_edge_pct':edge,'uncertainty':uncertainty,'tail_risk':tail,
            'specialist':meta.get('best_specialist'),'specialist_live_authority_pct':0}


def research_policy_decision(prediction, observation, *, open_positions=0, exposure_pct=0.0,
                             daily_loss_pct=0.0, kill_switch=False):
    # PAPER/RESEARCH only. Unknown honeypot may be researchable but is never execution-safe.
    meta=prediction['meta']
    edge=meta.get('net_edge_pct')
    uncertainty=meta.get('uncertainty')
    tail=meta.get('tail_risk')
    if any(v is None or not math.isfinite(v) for v in (edge,uncertainty,tail)):
        proposed='ABSTAIN'
    elif tail>=.25:
        proposed='VETO'
    else:
        proposed='TRADE' if edge>max(1.0,2*uncertainty) else 'ABSTAIN'
    state=SafetyState(security_ok=observation.get('security_researchable') is True,
        catastrophic_risk=bool(observation.get('hard_risk_seen')),
        provider_healthy=observation.get('feed_complete') is True and not observation.get('market_data_stale',True),
        liquidity_usd=observation.get('liquidity_usd'),exposure_pct=exposure_pct,
        open_positions=open_positions,daily_loss_pct=daily_loss_pct,kill_switch=kill_switch)
    result=hard_shadow_decision(proposed,state)
    return {**result,'proposed_action':proposed,'net_edge_pct':edge,'uncertainty':uncertainty,'tail_risk':tail,
            'specialist':meta.get('best_specialist'),'specialist_live_authority_pct':0,
            'decision_scope':'RESEARCH_PAPER'}


class NetCalibrator:
    """Validation-only Platt calibration of terminal after-cost profitability."""
    def __init__(self, weights=None):
        self.weights=np.array(weights if weights is not None else [0.,0.],dtype=float)
        self.fitted=weights is not None

    def fit(self, predictions):
        # First independent coin only, chosen before reading label sign.
        independent={}
        for record,prediction in sorted(predictions,key=lambda p:p[0]['observed_at']):
            independent.setdefault(record['coin_id'],(record,prediction))
        rows=[(r,p) for r,p in independent.values() if r['targets'].get('net_return') is not None and p['meta']['net_edge_pct'] is not None]
        if len(rows)<20 or len({r['targets']['net_return']>0 for r,p in rows})<2:
            return {'fitted':False,'independent_coins':len(rows),'reason':'insufficient_two_class_validation_coins'}
        x=np.array([[1.,np.clip(p['meta']['net_edge_pct']/10,-10,10)] for r,p in rows])
        y=np.array([r['targets']['net_return']>0 for r,p in rows],dtype=float)
        for _ in range(400):
            probabilities=1/(1+np.exp(-np.clip(<redacted-email>,-30,30)))
            self.weights-=.05*(x.T@(probabilities-y)/len(y)+.001*self.weights)
        self.fitted=True
        return {'fitted':True,'independent_coins':len(rows),'weights':self.weights.tolist()}

    def predict(self, prediction):
        edge=prediction['meta']['net_edge_pct']
        if not self.fitted or edge is None:
            return None
        logit=self.weights@[1.,np.clip(edge/10,-10,10)]
        return float(1/(1+np.exp(-np.clip(logit,-30,30))))


def evaluate_records(model, records, calibrator=None, *, bootstrap_samples=1000):
    evidence=[]
    action_counts={k:0 for k in ('TRADE','ABSTAIN','VETO')}
    per_target={}
    first={}
    for r in sorted(records,key=lambda r:r['observed_at']):
        first.setdefault(r['coin_id'],r)
    for r in first.values():
        p=model.predict(r['sequence'])
        decision=policy_decision(p,r['observation'])
        action_counts[decision['action']]+=1
        for k,label in r['targets'].items():
            predicted=p['targets'].get(k)
            if label is not None and predicted is not None:
                per_target.setdefault(k,[]).append((predicted-label)**2)
        gross=r['targets'].get('return_120s')
        evidence.append(TradeEvidence(coin=r['coin_id'],decision_ts=timestamp(r['observed_at']),
            outcome_ts=r['label_end_ts'] if gross is not None else None,gross_return_pct=gross,
            round_trip_cost_pct=r['cost_pct'] or 0,explorer_id=r.get('explorer_id'),
            specialist=decision['specialist'],regime=r['regime'],
            predicted_win_probability=calibrator.predict(p) if calibrator else None,
            model_hash=model.model_sha256 or 'unfrozen_research',source='REAL',action=decision['action']))
    report=evaluate_trades(evidence,bootstrap_samples=bootstrap_samples)
    report.update({'all_decisions':action_counts,'prediction_mse':{k:float(np.mean(v)) for k,v in per_target.items()},
        'prediction_label_counts':{k:len(v) for k,v in per_target.items()},
        'evaluation_kind':'historical_coin_disjoint_120s_shadow_horizon; not executed learned-exit portfolio',
        'eligible_for_promotion':False})
    return report


def training_run(source_root, runtime_dir, *, snapshot=False, max_records=24000, epochs=12,
                 dataset_cache=None, walk_forward=True):
    runtime=Path(runtime_dir).resolve()
    runtime.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_dir=runtime/'runs'/stamp
    run_dir.mkdir(parents=True,exist_ok=False)
    ledger=BrainLedger(runtime/'brain.db')
    try:
        assignments=ledger.metadata('initial_coin_assignments')
        historical=ledger.metadata('historical_coin_ids')
        if dataset_cache and Path(dataset_cache).is_file():
            dataset=json.loads(Path(dataset_cache).read_text(encoding='utf-8'))
        else:
            dataset=prepare_dataset(source_root,max_records=max_records,snapshot=snapshot,
                historical_coins=historical,assignments=assignments,
                progress=lambda p:print(canonical({'event':'dataset_source',**p}),flush=True))
            if dataset_cache:
                save_json(dataset_cache,dataset,immutable=True)
        if len(dataset['splits']['train'])<20:
            raise ValueError('Insufficient causal training sequences')
        if historical is None:
            ledger.freeze_metadata('historical_coin_ids',dataset['historical_coin_ids'])
            ledger.freeze_metadata('initial_coin_assignments',dataset['assignments'])
            ledger.freeze_metadata('forward_contract',{'explorer_historical_max':468,'explorer_forward_min':469,
                'unknown_coin_required':True,'live_authority_pct':0,'frozen_at':utc_now()})
        splits=dataset['splits']
        explorer_path=Path(source_root)/'runtime'/'explorer-paper.db'
        if explorer_path.exists():
            with open_readonly(explorer_path,snapshot=snapshot) as conn:
                dataset['audit']['explorer_max_id']=conn.execute('SELECT COALESCE(MAX(id),0) FROM explorer_positions').fetchone()[0]
        save_json(run_dir/'dataset_audit.json',dataset['audit'],immutable=True)
        if len(splits['train'])<20:
            raise ValueError('Insufficient causal training sequences')
        model=SequenceBrain(FEATURE_NAMES,hidden_size=24,seed=20260907)
        fit=model.fit(splits['train'],epochs=epochs,batch_size=64)
        model_path=run_dir/'challenger.json'
        calibrator=NetCalibrator()
        calibration=calibrator.fit([(r,model.predict(r['sequence'])) for r in splits['validation']])
        model.calibration=calibration
        model_hash=model.save(model_path)
        save_json(run_dir/'calibration.json',calibration,immutable=True)
        validation=evaluate_records(model,splits['validation'],calibrator)
        validation['evaluation_kind']='calibration_fit_partition_diagnostic_not_final_OOS'
        test=evaluate_records(model,splits['test'],calibrator)
        # Model architecture, seed, epoch count and entry policy are fixed before
        # opening test labels. Test metrics never trigger automatic promotion.
        wf=[]
        if walk_forward:
            research=splits['train']+splits['validation']
            coins=sorted({r['coin_id'] for r in research},key=lambda c:min(timestamp(r['observed_at']) for r in research if r['coin_id']==c))
            for fold in range(3):
                cut=max(1,len(coins)*(fold+2)//5)
                end=max(cut+1,len(coins)*(fold+3)//5)
                held=set(coins[cut:end])
                known=set(coins[:cut])
                boundary=min((timestamp(r['observed_at']) for r in research if r['coin_id'] in held),default=math.inf)
                train=[r for r in research if r['coin_id'] in known and r['label_end_ts']+120<boundary]
                holdout=[r for r in research if r['coin_id'] in held]
                if len(train)<20 or not holdout:
                    wf.append({'fold':fold,'status':'insufficient_purged_coins'})
                    continue
                fold_model=SequenceBrain(FEATURE_NAMES,hidden_size=24,seed=20260907+fold)
                fold_model.fit(train,epochs=epochs,batch_size=64)
                wf.append({'fold':fold,'train_records':len(train),'train_coins':len({r['coin_id'] for r in train}),
                           'test':evaluate_records(fold_model,holdout,bootstrap_samples=300)})
        registry=GovernorRegistry(runtime/'governor')
        try:
            sha=registry.register_challenger(model_path,training_coins=model.training_coins,
                validation_coins=[r['coin_id'] for r in splits['validation']],
                test_coins=[r['coin_id'] for r in splits['test']],historical_coins=dataset['all_coin_ids'])
            governor=registry.assess(sha)
        finally:
            registry.close()
        report={'version':VERSION,'created_at':utc_now(),'model_hash':model_hash,'model_path':str(model_path.relative_to(runtime)),
            'architecture':'causal_numpy_RNN_BPTT_7_neural_specialists_learned_meta_gate_16_masked_heads',
            'execution_authority_pct':0,'cost_policy':CostPolicy().as_dict(),'dataset':dataset['audit'],
            'training':fit,'calibration':calibration,'validation':validation,'test':test,'walk_forward':wf,
            'governor':governor,'champion':governor['champion'],'challenger':model_hash,
            'forward_baseline':468,'forward_first_id':469,
            'known_weaknesses':['Historical research only; no prospective v1 forward evidence yet',
                'Provider repeated prices and absent security/liquidity limit executable evidence',
                'Crash labels are observed price-collapse proxies, not verified on-chain rug labels',
                'Specialist names express loss emphasis, not proven independent specialist edges',
                'Cross-specialist disagreement is not calibrated uncertainty',
                'Cost policy is conservative assumed paper cost, not verified live fills',
                'Reported horizon PF is not a learned-exit portfolio backtest',
                'Synthetic databases are inventoried and kept as research diagnostics; no simulation promotion']}
        save_json(run_dir/'report.json',report,immutable=True)
        save_json(runtime/'latest_report.json',report)
        save_json(runtime/'active_challenger.json',{'model_hash':model_hash,'model_path':str(model_path.relative_to(runtime)),
            'calibration_path':str((run_dir/'calibration.json').relative_to(runtime)),'execution_authority_pct':0})
        ledger.event('CHALLENGER_TRAINED',{'model_hash':model_hash,'run_dir':str(run_dir),'promotion_eligible':governor['eligible']})
        return report
    finally:
        ledger.close()
