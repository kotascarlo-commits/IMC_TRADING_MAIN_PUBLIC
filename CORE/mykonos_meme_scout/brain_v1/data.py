"""Causal raw-market adapters. Source DBs are never migrated or written.

Rolling five-minute counters remain rolling counters, never alleged one-second
executions. Unknown endpoints and path gaps mask labels rather than fabricate
prices. Coin identities are global across databases, sessions and policies.
"""
from __future__ import annotations
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .costs import CostPolicy, net_return
from ..compact_storage import logical_table_names

HORIZONS = (5, 15, 30, 60, 120)
FEATURE_NAMES = (
    'log_price','return_from_window_start','velocity','acceleration',
    'log_liquidity','liquidity_velocity','liquidity_known','log_market_cap',
    'log_volume_5m','volume_velocity','volume_acceleration','log_transactions_5m',
    'transaction_change','buy_ratio','rolling_net_flow','buy_ratio_change',
    'momentum_5m','pullback_from_past_peak','age_seconds','security_known',
    'hard_risk_seen','provider_complete','stale','feed_latency','elapsed_seconds',
    'price_unchanged','flow_known','market_cap_known','position_gain',
    'position_peak_gain','position_giveback','position_elapsed_seconds',
)


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None:
        raise ValueError('Naive observation timestamp')
    return result.timestamp()


def number(value, default=0.0):
    try:
        n = float(value)
        return n if math.isfinite(n) else default
    except (TypeError, ValueError):
        return default


@contextmanager
def open_readonly(path, *, snapshot=False):
    path = Path(path).resolve()
    # immutable is permitted ONLY for explicit quiescent backup files.
    if snapshot and any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal')):
        raise ValueError('Snapshot must not have an active journal')
    uri = path.as_uri() + '?mode=ro' + ('&immutable=1' if snapshot else '')
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        yield conn
    finally:
        conn.close()


def normalize_observation(raw, *, source_domain='REAL', source_path=''):
    raw = dict(raw)
    if 'payload_json' in raw:
        payload = json.loads(raw['payload_json'])
        payload['captured_at'] = raw['captured_at']
        raw = payload
    sec = raw.get('security') or {}
    checked = bool(raw.get('security_checked', sec.get('checked', False)))
    honeypot_known = sec.get('honeypot') is True or sec.get('honeypot') is False
    authority_fields_known = (
        sec.get('mint_authority_disabled') is True or sec.get('mint_authority_disabled') is False
    ) and (
        sec.get('freeze_authority_disabled') is True or sec.get('freeze_authority_disabled') is False
    )
    risk = bool(raw.get('hard_risk_seen', False)) or any(sec.get(k) is True for k in
        ('honeypot','non_transferable','transfer_hook_present','malicious_creator'))
    risk = risk or any(sec.get(k) is False for k in ('mint_authority_disabled','freeze_authority_disabled'))
    research_security_ok = (
        checked and authority_fields_known and not risk
        and sec.get('mint_authority_disabled') is True
        and sec.get('freeze_authority_disabled') is True
    )
    execution_security_ok = (
        research_security_ok and honeypot_known and sec.get('honeypot') is False
    )
    captured = str(raw['captured_at'])
    ts = timestamp(captured)
    liquidity = number(raw.get('liquidity_usd'))
    feed = str(raw.get('feed_source',raw.get('discovery_source','unknown')))
    complete = bool(raw.get('feed_complete', True)) and feed not in ('unknown','partial')
    age = number(raw.get('age_seconds'), max(0, ts-number(raw.get('pair_created_at_ms'))/1000))
    return {'coin_id':str(raw.get('address',raw.get('coin_id',''))),
            'pair_address':str(raw.get('pair_address','')), 'captured_at':captured,'ts':ts,
            'price_usd':number(raw.get('price_usd')), 'liquidity_usd':liquidity,
            'market_cap_usd':number(raw.get('market_cap_usd')),
            'volume_5m_usd':number(raw.get('volume_5m_usd')),
            'buys_5m':number(raw.get('buys_5m')), 'sells_5m':number(raw.get('sells_5m')),
            'price_change_5m_pct':number(raw.get('price_change_5m_pct')),
            'age_seconds':max(0,age),'security_checked':checked,'hard_risk_seen':risk,
            # Distinguish research eligibility from execution safety.
            # Unknown honeypot status may be observed in PAPER/RESEARCH, but never treated as execution-safe.
            'security_researchable':research_security_ok,
            'security_ok':execution_security_ok,
            'honeypot_known':honeypot_known,
            'feed_complete':complete, 'market_data_stale':bool(raw.get('market_data_stale',False)),
            'feed_latency_ms':number(raw.get('feed_latency_ms')),
            'source_domain':source_domain, 'source_path':source_path}


def build_sequence(observations, entry_context=None):
    rows = list(observations)
    if not rows:
        raise ValueError('Empty sequence')
    if any(r['price_usd'] <= 0 for r in rows):
        raise ValueError('Nonpositive price')
    if len({r['coin_id'] for r in rows}) != 1 or len({r.get('pair_address','') for r in rows}) != 1:
        raise ValueError('Mixed coin/pair sequence')
    if any(b['ts'] <= a['ts'] for a,b in zip(rows,rows[1:])):
        raise ValueError('Sequence must have strictly increasing observation times')
    start_price = rows[0]['price_usd']
    entry_price = number((entry_context or {}).get('entry_price'),start_price)
    entry_ts = number((entry_context or {}).get('entry_ts'),rows[0]['ts'])
    if entry_price <= 0 or entry_ts > rows[0]['ts']:
        raise ValueError('Entry context must precede the sequence')
    if entry_context and number(entry_context.get('known_at_ts'), math.inf) > rows[0]['ts']:
        raise ValueError('Position peak must be known before the sequence')
    past_peak = max(entry_price,number((entry_context or {}).get('peak_price'),entry_price))
    result, old_velocity, old_volume_velocity = [], 0.0, 0.0
    for i,r in enumerate(rows):
        prior = rows[max(0,i-1)]
        dt = max(.001,r['ts']-prior['ts']) if i else 1.0
        velocity = (r['price_usd']/prior['price_usd']-1)*100/dt if i else 0
        volume_v = (r['volume_5m_usd']-prior['volume_5m_usd'])/dt if i else 0
        liq_v = (r['liquidity_usd']/prior['liquidity_usd']-1)*100/dt if i and min(r['liquidity_usd'],prior['liquidity_usd'])>0 else 0
        transactions = r['buys_5m']+r['sells_5m']
        prior_transactions = prior['buys_5m']+prior['sells_5m']
        buy_ratio = r['buys_5m']/transactions if transactions else .5
        old_ratio = prior['buys_5m']/prior_transactions if prior_transactions else .5
        past_peak = max(past_peak,r['price_usd'])
        gain = (r['price_usd']/entry_price-1)*100
        peak_gain = (past_peak/entry_price-1)*100
        values = [math.log(r['price_usd']), (r['price_usd']/start_price-1)*100,
            velocity,(velocity-old_velocity)/dt if i else 0,
            math.log1p(max(0,r['liquidity_usd'])),liq_v,float(r['liquidity_usd']>0),
            math.log1p(max(0,r['market_cap_usd'])),math.log1p(max(0,r['volume_5m_usd'])),
            volume_v,(volume_v-old_volume_velocity)/dt if i else 0,
            math.log1p(max(0,transactions)),(transactions-prior_transactions)/dt if i else 0,
            buy_ratio,r['buys_5m']-r['sells_5m'],buy_ratio-old_ratio,
            r['price_change_5m_pct'],(r['price_usd']/past_peak-1)*100,r['age_seconds'],
            float(r['security_checked']),float(r['hard_risk_seen']),float(r['feed_complete']),
            float(r['market_data_stale']),r['feed_latency_ms'],dt,
            float(i>0 and r['price_usd']==prior['price_usd']),float(transactions>0),
            float(r['market_cap_usd']>0),gain,peak_gain,peak_gain-gain,max(0,r['ts']-entry_ts)]
        result.append([max(-1e6,min(1e6,number(v))) for v in values])
        old_velocity, old_volume_velocity = velocity,volume_v
    return result


def make_records(observations, *, seq_len=16, stride_seconds=30, max_per_coin=24, cost_policy=None):
    policy = cost_policy or CostPolicy()
    rows = sorted(observations,key=lambda r:r['ts'])
    if len({r['coin_id'] for r in rows}) > 1:
        raise ValueError('Labels require a single coin identity')
    unique = {(r['coin_id'],r.get('pair_address',''),r['ts']):r for r in rows if r['price_usd']>0}
    rows = sorted(unique.values(),key=lambda r:r['ts'])
    times = [r['ts'] for r in rows]
    output=[]
    last=-math.inf
    for i in range(seq_len-1,len(rows)):
        row = rows[i]
        if row['market_data_stale']:
            continue
        if row['ts']-last<stride_seconds:
            continue
        window=rows[i-seq_len+1:i+1]
        if len({r.get('pair_address','') for r in window})!=1 or any(b['ts']-a['ts']>30 for a,b in zip(window,window[1:])):
            continue
        targets={}
        endpoints={}
        label_end=row['ts']
        for h in HORIZONS:
            j=bisect_left(times,row['ts']+h,i+1)
            good=j<len(rows) and 0<=rows[j]['ts']-row['ts']-h<=1.0 and rows[j].get('pair_address','')==row.get('pair_address','') and not rows[j]['market_data_stale']
            targets[f'return_{h}s']=(rows[j]['price_usd']/row['price_usd']-1)*100 if good else None
            if good:
                endpoints[h]=j
                label_end=max(label_end,rows[j]['ts'])
        for name in ('mfe','mae','p5','p10','p25','p_crash','p_liquidity_collapse','p_stagnation','net_return','exit_now','continuation'):
            targets[name]=None
        cost=policy.estimate(row['liquidity_usd'])
        if 120 in endpoints:
            targets['net_return']=net_return(targets['return_120s'],cost) if cost else None
            path=rows[i:endpoints[120]+1]
            dense=all(b['ts']-a['ts']<=3 for a,b in zip(path,path[1:])) and not any(r['market_data_stale'] for r in path) and all(r.get('pair_address','')==row.get('pair_address','') for r in path)
            if dense:
                returns=[(r['price_usd']/row['price_usd']-1)*100 for r in path]
                targets.update(mfe=max(returns),mae=min(returns),p5=float(max(returns)>=5),p10=float(max(returns)>=10),p25=float(max(returns)>=25),p_crash=float(min(returns)<=-30),p_stagnation=float(max(returns)-min(returns)<2))
                if all(r['liquidity_usd']>0 and r['feed_complete'] for r in path):
                    targets['p_liquidity_collapse']=float(min(r['liquidity_usd'] for r in path)<row['liquidity_usd']*.5)
        if 30 in endpoints and cost:
            # Incremental hold value; no future peak or ideal exit as input.
            hold_value=net_return(targets['return_30s'],cost/2)
            targets['continuation']=float(hold_value>0)
            targets['exit_now']=float(hold_value<=0)
        if not any(v is not None for v in targets.values()):
            continue
        last=row['ts']
        output.append({'coin_id':row['coin_id'],'observed_at':row['captured_at'],'timestamp':row['captured_at'],
            'label_end_ts':label_end,'source_domain':row['source_domain'], 'source_path':row['source_path'],
            'sequence':build_sequence(window),'targets':targets,'observation':row,
            'cost_pct':cost,'sequence_start_ts':window[0]['ts'],
            'regime':'positive_momentum' if row['price_change_5m_pct']>5 else 'negative_momentum' if row['price_change_5m_pct']<-5 else 'flat',
            'sample_weight':1.0})
        if len(output)>=max_per_coin:
            break
    return output


def split_records(records, *, forward_coins=(), assignments=None, purge_seconds=120):
    forward=set(forward_coins)
    first={}
    for r in records:
        if r['coin_id'] not in forward:
            first[r['coin_id']]=min(first.get(r['coin_id'],math.inf),timestamp(r['observed_at']))
    ordered=sorted(first,key=lambda c:(first[c],c))
    a,b=int(len(ordered)*.6),int(len(ordered)*.8)
    mapping=assignments or {c:('train' if i<a else 'validation' if i<b else 'test') for i,c in enumerate(ordered)}
    boundaries={name:min((timestamp(r['observed_at']) for r in records if mapping.get(r['coin_id'])==name),default=math.inf) for name in ('validation','test')}
    splits={k:[] for k in ('train','validation','test','forward','purged')}
    for r in records:
        c=r['coin_id']
        role='forward' if c in forward else mapping.get(c,'train')
        boundary=boundaries.get('validation' if role=='train' else 'test' if role=='validation' else '',math.inf)
        if r['label_end_ts']+purge_seconds>=boundary:
            role='purged'
        splits[role].append(r)
    return splits,mapping


def source_databases(root):
    root=Path(root)
    return sorted(p for p in root.rglob('*.db') if not any('backup' in part.lower() or part in ('brain-v1','brain_v1') for part in p.relative_to(root).parts))


def prepare_dataset(root, *, max_records=24000, sequence_length=16, stride_seconds=30,
                    historical_coins=None, assignments=None, snapshot=False, progress=None):
    root=Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    records=[]
    all_coins=set()
    source_report=[]
    explorer_min={}
    explorer_max=0
    total_counts=Counter()
    # All databases are inventoried; simulations are separate research data,
    # never relabeled as observed price evidence.
    for path in source_databases(root):
        with open_readonly(path,snapshot=snapshot) as conn:
            tables=logical_table_names(conn)
            if 'simulation_metadata' in tables:
                source_report.append({'path':str(path.relative_to(root)), 'role':'SIMULATION',
                    'used_for_market_validation':False})
                continue
            if 'explorer_positions' in tables:
                explorer_max=max(explorer_max,conn.execute('SELECT COALESCE(MAX(id),0) FROM explorer_positions').fetchone()[0])
                for r in conn.execute('SELECT address,MIN(id) first_id FROM explorer_positions GROUP BY address'):
                    explorer_min[r['address']]=min(explorer_min.get(r['address'],10**18),r['first_id'])
            table='real_temporal_ticks' if 'real_temporal_ticks' in tables else 'snapshots' if 'snapshots' in tables else None
            if table is None:
                source_report.append({'path':str(path.relative_to(root)), 'role':'SIMULATION' if any(t.startswith('simulation_') for t in tables) else 'AUXILIARY','used_for_market_validation':False})
                continue
            info={'path':str(path.relative_to(root)),'role':'REAL_RAW','rows':0,'records':0}
            current=None
            observations=[]
            def emit():
                if observations:
                    new=make_records(observations,seq_len=sequence_length,stride_seconds=stride_seconds,max_per_coin=24)
                    records.extend(new)
                    info['records']+=len(new)
            for raw in conn.execute(f'SELECT * FROM {table} ORDER BY address,captured_at'):
                info['rows']+=1
                try:
                    obs=normalize_observation(raw,source_path=info['path'])
                except (ValueError,TypeError,KeyError):
                    total_counts['invalid_observations']+=1
                    continue
                all_coins.add(obs['coin_id'])
                if current!=obs['coin_id']:
                    emit()
                    current=obs['coin_id']
                    observations=[]
                observations.append(obs)
            emit()
            source_report.append(info)
            total_counts['raw_observations']+=info['rows']
            if progress:
                progress(info)
    historical=set(historical_coins) if historical_coins is not None else set(all_coins)
    historical.update(c for c,n in explorer_min.items() if n<=468)
    forward={c for c,n in explorer_min.items() if n>=469 and c not in historical}
    by_coin=defaultdict(dict)
    for r in records:
        r['explorer_id']=explorer_min.get(r['coin_id'])
        # Source priority is predetermined (raw snapshot before temporal for
        # identical timestamps), never chosen using return or policy results.
        by_coin[r['coin_id']].setdefault(r['observed_at'],r)
    # Coin-balanced deterministic cap includes every eligible coin when capacity
    # permits; extra records per coin do not create independent evidence.
    selected=[]
    sorted_coins=sorted(by_coin,key=lambda c:hashlib.sha256(c.encode()).hexdigest())
    per_coin=max(1,max_records//max(1,len(sorted_coins)))
    for c in sorted_coins:
        selected.extend(sorted(by_coin[c].values(),key=lambda r:r['observed_at'])[:per_coin])
    selected=selected[:max_records]
    splits,mapping=split_records(selected,forward_coins=forward,assignments=assignments)
    masks=Counter(k for r in selected for k,v in r['targets'].items() if v is not None)
    audit={'sources':source_report,**dict(total_counts),'unique_observed_coins':len(all_coins),
        'eligible_sequence_coins':len(by_coin),'selected_unique_coins':len({r['coin_id'] for r in selected}),
        'candidate_records_before_cap':len(records),'selected_records':len(selected),'max_records':max_records,
        'label_counts':dict(masks),'forward_baseline':468,'forward_min_id':469,
        'forward_unknown_coins':len(forward),'explorer_max_id':explorer_max,
        'splits':{k:{'records':len(v),'unique_coins':len({r['coin_id'] for r in v})} for k,v in splits.items()},
        'legacy_outcome_labels_used':False,'simulation_economics_used':False,'execution_authority_pct':0}
    return {'records':selected,'splits':splits,'audit':audit,'historical_coin_ids':sorted(historical),
            'all_coin_ids':sorted(all_coins),'assignments':mapping}
