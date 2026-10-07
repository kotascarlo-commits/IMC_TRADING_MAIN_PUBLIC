"""Continuing synthetic stress research, permanently excluded from promotion."""
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import time

from .data import FEATURE_NAMES, make_records, normalize_observation, open_readonly
from .model import SequenceBrain
from .pipeline import save_json


def simulate_research(source_root,runtime):
    from ..simulation import run_simulation
    runtime=Path(runtime)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=runtime/'simulations'/stamp
    output.mkdir(parents=True,exist_ok=False)
    db=output/'simulation.db'
    result=run_simulation(db,seed=int(time.time()),duration_seconds=300,synthetic_per_family=1)
    archive_families=Counter()
    distinct_paths=set()
    archive_count=0
    for path in sorted((Path(source_root)/'data'/'simulations').glob('*.db')):
        with open_readonly(path,snapshot=(Path(source_root)/'BACKUP_MANIFEST.json').exists()) as conn:
            for row in conn.execute('SELECT family,sequence_checksum FROM simulation_scenarios'):
                archive_count+=1
                if row['sequence_checksum'] not in distinct_paths:
                    distinct_paths.add(row['sequence_checksum']);archive_families[row['family']]+=1
    records=[]
    with open_readonly(db) as conn:
        for scenario in conn.execute("SELECT scenario_id,family FROM simulation_scenarios WHERE origin='SYNTHETIC'").fetchall():
            rows=[];rolling=deque()
            for tick in conn.execute('SELECT * FROM simulation_ticks WHERE scenario_id=? ORDER BY second_offset',(scenario['scenario_id'],)):
                rolling.append(tick)
                while rolling[0]['second_offset']<tick['second_offset']-299:rolling.popleft()
                raw={**dict(tick),'address':'SIM:'+scenario['scenario_id'],'pair_address':scenario['scenario_id'],
                    'volume_5m_usd':sum(r['volume_1s_usd'] for r in rolling),
                    'buys_5m':sum(r['buys_1s'] for r in rolling),'sells_5m':sum(r['sells_1s'] for r in rolling),
                    'age_seconds':tick['second_offset'],'feed_source':'synthetic_known','feed_complete':True,
                    'security_checked':False,'hard_risk_seen':bool(tick['rug_event'])}
                rows.append(normalize_observation(raw,source_domain='SIMULATION',source_path=str(db)))
            records.extend(make_records(rows,max_per_coin=5))
    fit=None
    if records:
        model=SequenceBrain(FEATURE_NAMES,hidden_size=16,seed=123)
        fit=model.fit(records,epochs=3,batch_size=32)
        model.save(output/'simulation_research_model.json')
    report={'data_domain':'SIMULATION','execution_authority_pct':0,'eligible_for_promotion':False,
            'archive_scenarios_reviewed':archive_count,'unique_archived_paths':len(distinct_paths),
            'archive_families':dict(archive_families),'new_run':result,'training':fit}
    save_json(output/'report.json',report,immutable=True)
    save_json(runtime/'simulation_status.json',report)
    return report
