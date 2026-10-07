"""Incremental exit refinement using retained causal windows and fixed splits."""
from datetime import datetime,timezone
from pathlib import Path
from math import exp
import json
from collections import defaultdict
from . import VERSION
from .data import FEATURE_NAMES,open_readonly,normalize_observation,make_records
from .exit_training import augment_exit_records
from .ledger import BrainLedger,utc_now
from .governor import GovernorRegistry
from .pipeline import NetCalibrator,evaluate_records,save_json
from .runtime import load_active
from .online import training_lock


def reconstruct_observed_window(record):
    rows=[];ts=record['observation']['ts']
    for vector in reversed(record['sequence']):
        rows.append({**record['observation'],'ts':ts,'captured_at':datetime.fromtimestamp(ts,timezone.utc).isoformat(),
            'price_usd':exp(vector[FEATURE_NAMES.index('log_price')]),
            'market_data_stale':bool(vector[FEATURE_NAMES.index('stale')])})
        ts-=vector[FEATURE_NAMES.index('elapsed_seconds')]
    return list(reversed(rows))


def refine_exits(dataset_cache,runtime,*,epochs=6):
    runtime=Path(runtime)
    with training_lock(runtime):
        info,model,_=load_active(runtime)
        dataset=json.loads(Path(dataset_cache).read_text(encoding='utf-8'))
        splits=dataset['splits']
        if not {r['coin_id'] for r in splits['train']}.issubset(model.training_coins):
            raise ValueError('Refinement must retain the existing training partition')
        if model.training_coins & {r['coin_id'] for k in ('validation','test') for r in splits[k]}:
            raise ValueError('Training/holdout identity overlap')
        extras=[]
        for record in splits['train']:
            extras.extend(augment_exit_records([record],{record['coin_id']:reconstruct_observed_window(record)})[1:])
        if len(extras)<20:raise ValueError('Insufficient observed held-position examples')
        fit=model.fit(extras,epochs=epochs,batch_size=64,learning_rate=.001)
        cal=NetCalibrator();calibration=cal.fit([(r,model.predict(r['sequence'])) for r in splits['validation']])
        model.calibration=calibration
        run=runtime/'runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');run.mkdir(parents=True)
        path=run/'challenger.json';sha=model.save(path)
        save_json(run/'calibration.json',calibration,immutable=True)
        historical=evaluate_records(model,splits['test'],cal)
        historical['evaluation_kind']='previously_inspected_historical_test_diagnostic_not_new_forward_evidence'
        groups=defaultdict(list)
        if (runtime/'market.db').exists():
            with open_readonly(runtime/'market.db') as conn:
                for raw in conn.execute('SELECT * FROM snapshots ORDER BY address,captured_at'):
                    groups[raw['address']].append(normalize_observation(raw))
        fresh=[r for rows in groups.values() for r in make_records(rows)]
        new_market=evaluate_records(model,fresh,cal)
        new_market['evaluation_kind']='post_fit_snapshot_diagnostic_not_preregistered_forward_evidence'
        registry=GovernorRegistry(runtime/'governor')
        try:
            registry.register_challenger(path,training_coins=model.training_coins,
                historical_coins=set(dataset['all_coin_ids'])|set(groups))
            gate=registry.assess(sha)
        finally:registry.close()
        report={'version':VERSION,'at':utc_now(),'parent_hash':info['model_hash'],'challenger':sha,
            'champion':gate['champion'],'execution_authority_pct':0,'training':fit,
            'original_splits':dataset['audit']['splits'],'additional_exit_examples':len(extras),
            'calibration':calibration,'historical_test':historical,'new_market_test':new_market,
            'new_market_coins':len(groups),'governor':gate,'model_path':str(path.relative_to(runtime)),
            'no_test_labels_used_for_training':True,'forward_baseline':468,
            'promotion_metric_scope':'120s_entry_horizon_only; learned_exit_portfolio_requires_separate_evidence'}
        save_json(run/'refinement_report.json',report,immutable=True)
        save_json(runtime/'refinement_report.json',report)
        ledger=BrainLedger(runtime/'brain.db')
        try:ledger.event('EXIT_REFINEMENT',{'model_hash':sha,'parent_hash':info['model_hash'],'training_coins':fit['unique_coins']})
        finally:ledger.close()
        save_json(runtime/'active_challenger.json',{'model_hash':sha,'model_path':str(path.relative_to(runtime)),
            'calibration_path':str((run/'calibration.json').relative_to(runtime)),'execution_authority_pct':0})
        return report
