"""Offline parameter research. No provider, wallet, execution or live-config writes.

All historical fills are deliberately labelled price proxies, not executable
quotes. Unknown holder/freshness evidence is retained and never synthesized.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'CORE'))
from mykonos_meme_scout.config import Config
from mykonos_meme_scout.paper_forward import brain

RUN_ID = '8096b3356bd944ee957b314578143e59'
DOMAIN = 'HISTORICAL_PRICE_PROXY_WITH_MISSING_SECURITY_NOT_EXECUTABLE'
REQUIRED_FLAGS = {'checked': True, 'mint_authority_disabled': True,
                  'freeze_authority_disabled': True, 'non_transferable': False,
                  'transfer_hook_present': False, 'malicious_creator': False}
DELAY, WAIT, GAP, PURGE = 2.0, 10.0, 30.0, 400.0


def epoch(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timezone required')
    return d.timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def readonly(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    c.execute('PRAGMA query_only=ON')
    c.execute('PRAGMA cache_size=-16384')
    return c


@dataclass(frozen=True)
class Parameters:
    name: str
    liquidity: float = 100000
    buy_ratio: float = .75
    momentum: float = 20
    momentum_max: float = 120
    age: float = 15
    score: int = 72
    volume_ratio: float = .5
    hold: float = 180
    stop: float = 12
    take: float = 20
    trailing_arm: float = 10
    trailing_stop: float = 6
    fraction: float = .10


def grid():
    base = Parameters('baseline')
    entries = [base]
    for name, field, values in [
        ('liquidity', 'liquidity', [30000, 60000]),
        ('buy', 'buy_ratio', [.60, .70]),
        ('momentum', 'momentum', [5, 10]),
        ('score', 'score', [60, 66]),
        ('volume', 'volume_ratio', [.12, 1.0]),
        ('early', 'age', [5]),
    ]:
        for value in values:
            entries.append(replace(base, name=f'{name}_{value}', **{field: value}))
    entries += [replace(base, name='balanced', liquidity=60000, buy_ratio=.70,
                        momentum=10, score=66),
                replace(base, name='early_flow', liquidity=30000, buy_ratio=.70,
                        momentum=10, momentum_max=60, score=66, age=5)]
    exits = [('current', {}), ('tight', {'hold': 90, 'stop': 8, 'take': 15,
                                       'trailing_arm': 8, 'trailing_stop': 4}),
             ('patient', {'hold': 300, 'stop': 12, 'take': 30,
                          'trailing_arm': 15, 'trailing_stop': 8})]
    return [replace(e, name=f'{e.name}/{n}/{fraction}', fraction=fraction, **x)
            for e in entries for n, x in exits for fraction in [.05, .10]]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + '\n', encoding='utf-8')


def make_plan(source, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'plan.json'
    if target.exists():
        raise ValueError('plan already frozen; choose a new output directory')
    with closing(readonly(source)) as c:
        upper, last = c.execute('SELECT id,captured_at FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
        lower, first = c.execute('SELECT id,captured_at FROM snapshots ORDER BY id LIMIT 1').fetchone()
    with closing(readonly(ROOT / 'RUNTIME/reality-run.db')) as c:
        run = json.loads(c.execute('SELECT doc FROM runs WHERE id=?', (RUN_ID,)).fetchone()[0])
    sources = ['RESEARCH/parameter_study.py', 'CORE/mykonos_meme_scout/paper_forward.py',
               'CORE/mykonos_meme_scout/scoring.py', 'CORE/mykonos_meme_scout/config.py',
               'CORE/mykonos_meme_scout/models.py']
    plan = {'domain': DOMAIN, 'source': str(Path(source).resolve()), 'lower_id': lower,
            'upper_id': upper, 'first': first, 'last': last,
            'code_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
            'frozen_run_policy_hash': run['policy_hash'], 'entry_config': run['policy']['entry'],
            'internal_scoring_config': asdict(Config()), 'grid': [asdict(p) for p in grid()],
            'splits': {'train': [first, '2026-09-28T00:00:00+00:00'],
                       'validation': ['2026-09-28T00:00:00+00:00', '2026-10-02T00:00:00+00:00'],
                       'test': ['2026-10-02T00:00:00+00:00', last]},
            'purge_seconds': PURGE, 'delay_seconds': DELAY, 'fill_wait_seconds': WAIT,
            'max_observation_gap_seconds': GAP, 'candidate_spacing_seconds': 2,
            'capital_usd': 1000, 'fee_fraction_per_side': .013,
            'slippage_floor': .0035, 'network_usd_per_side': .12,
            'extra_cost_per_side': [0, .01, .03],
            'minimum_trades_train': 30, 'minimum_trades_validation': 20,
            'minimum_unique_tokens_validation': 20,
            'minimum_trades_final_test': 30, 'minimum_test_days': 3,
            'selection': 'maximise minimum train/validation mean net PnL at +1%/side; require positive both, PF>=1.2 both; else abstain',
            'diagnostic_lane': 'Only absent holder values and absent security.checked_at may be ignored. No replacement values. Known unsafe flags, stale security, core hard failures and all other brain gates retained.',
            'caveats': ['captured_at is not a receipt timestamp: historical availability unverified',
                        'prices/liquidity are not executable quotes or guaranteed stops',
                        'fees and price impact are assumptions, not measured route costs',
                        'historical watcher aggregate results have previously been viewed',
                        'source append-only upper-ID fence; selected source rows hash-identified'],
            'one_trade_per_token_per_split': True,
            'simultaneous_priority': 'event time ascending, canonical score descending, snapshot ID ascending'}
    write_json(target, plan)
    print(json.dumps({'phase': 'PLAN_FROZEN', 'configs': len(plan['grid']), 'plan_sha256': digest(plan)}), flush=True)


def verify_plan(plan):
    for p, h in plan['code_sha256'].items():
        if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h:
            raise ValueError('code changed after plan freeze: ' + p)


def feature(row, previous, cfg):
    sid, _, payload = row
    current = json.loads(payload)
    t = epoch(current['captured_at'])
    evidence = {}
    reasons = set(brain(current, previous, cfg, t, evidence=evidence))
    sec = current.get('security', {})
    ignored = set()
    for k in ('creator_holder_pct', 'top10_holder_pct'):
        if sec.get(k) is None and 'security:' + k in reasons:
            ignored.add('security:' + k)
    if sec.get('checked_at') is None and 'security_timestamp_unverified' in reasons:
        ignored.add('security_timestamp_unverified')
    return {'id': sid, 't': t, 'mint': current['address'],
            'liq': current['liquidity_usd'], 'buy': current['buys_5m'] / max(1, current['buys_5m'] + current['sells_5m']),
            'momentum': current['price_change_5m_pct'],
            'age': max(0, (t - current['pair_created_at_ms'] / 1000) / 60),
            'volume_ratio': current['volume_5m_usd'] / max(current['liquidity_usd'], 1),
            'score': evidence.get('score', -1), 'strict_static': sorted(reasons),
            'diagnostic_static': sorted(reasons - ignored), 'missing_evidence': sorted(ignored)}


def extract(out):
    out = Path(out)
    plan = json.loads((out / 'plan.json').read_text())
    verify_plan(plan)
    if (out / 'dataset.json').exists():
        raise ValueError('dataset already frozen')
    c = readonly(plan['source'])
    cfg = dict(plan['entry_config'], minimum_liquidity_usd=30000,
               minimum_buy_ratio=.60, minimum_momentum_pct=5, maximum_momentum_pct=120,
               maximum_entry_age_minutes=15, minimum_score=0, maximum_volume_liquidity_ratio=1)
    events, counts, days, last_event = [], Counter(), defaultdict(Counter), {}
    coverage_tokens = defaultdict(set)
    selected_hash = hashlib.sha256()
    started = time.monotonic()
    # The minimum envelope covers every grid hypothesis. Never uses future returns.
    sql = """SELECT id,address,payload_json FROM snapshots WHERE id>? AND id<=?
        AND price_usd>0 AND json_extract(payload_json,'$.liquidity_usd')>=30000
        AND json_extract(payload_json,'$.pair_created_at_ms')>0
        AND json_extract(payload_json,'$.price_change_5m_pct') BETWEEN 5 AND 120
        AND json_extract(payload_json,'$.buys_5m') >= .60 *
          max(1,json_extract(payload_json,'$.buys_5m')+json_extract(payload_json,'$.sells_5m'))
        ORDER BY id"""
    upper = plan['upper_id']
    # Bounded read transactions release locks every 100k source IDs.
    for lo in range(plan['lower_id'] - 1, upper, 100000):
        hi = min(upper, lo + 100000)
        for row in c.execute(sql, (lo, hi)):
            sid, mint, payload = row
            v = json.loads(payload)
            t = epoch(v['captured_at']); age = (t-v['pair_created_at_ms']/1000)/60
            if not 0 <= age <= 15:
                continue
            day = v['captured_at'][:10]
            counts['envelope_observations'] += 1; days[day]['envelope_observations'] += 1
            coverage_tokens[day].add(mint)
            sec = v.get('security', {})
            for key in ('checked_at', 'top10_holder_pct', 'creator_holder_pct'):
                if sec.get(key) is None:
                    counts['missing_' + key] += 1; days[day]['missing_' + key] += 1
            if not all(sec.get(k) is value for k, value in REQUIRED_FLAGS.items()):
                counts['other_security_flags_not_verified'] += 1
                continue
            if sec.get('honeypot') is True:
                counts['known_honeypot'] += 1; continue
            counts['known_security_flags'] += 1; days[day]['known_security_flags'] += 1
            if t-last_event.get(mint, -math.inf) < plan['candidate_spacing_seconds']:
                counts['causal_spacing_skips'] += 1; continue
            last_event[mint] = t
            # Match the actual runner's prior ordinal snapshot, including same-time rows.
            prev = c.execute('SELECT id,payload_json FROM snapshots WHERE address=? AND id<? ORDER BY id DESC LIMIT 1', (mint, sid)).fetchone()
            previous = json.loads(prev[1]) if prev else None
            f = feature(row, previous, cfg)
            selected_hash.update(str(sid).encode()+payload.encode())
            if prev:
                selected_hash.update(str(prev[0]).encode()+prev[1].encode())
            if not f['strict_static']:
                counts['strict_static_eligible'] += 1
            if not f['diagnostic_static']:
                events.append(f); counts['diagnostic_events'] += 1
                days[day]['diagnostic_events'] += 1
        print(json.dumps({'phase': 'EXTRACT', 'source_ids_read': hi-plan['lower_id']+1,
                          'source_ids_total': upper-plan['lower_id']+1,
                          'diagnostic_events': len(events), 'elapsed_seconds': round(time.monotonic()-started, 1)}), flush=True)
    # Fetch only merged bounded outcome windows; missing/low-liquidity rows remain.
    windows = defaultdict(list)
    for e in events:
        windows[e['mint']].append((e['t'], e['t']+PURGE))
    paths = {}
    for mint, ranges in sorted(windows.items()):
        merged = []
        for a, b in sorted(ranges):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a, b])
        rows = {}
        for a, b in merged:
            for sid, stamp, price, payload in c.execute('SELECT id,captured_at,price_usd,payload_json FROM snapshots WHERE address=? AND captured_at>=? AND captured_at<=? AND id<=? ORDER BY captured_at,id', (mint, iso(a), iso(b), upper)):
                v = json.loads(payload)
                selected_hash.update(str(sid).encode()+payload.encode())
                liq = v.get('liquidity_usd')
                numeric = all(type(x) in (int, float) and math.isfinite(x) for x in (price, liq))
                valid = numeric and v.get('market_data_stale') is False and price == v.get('price_usd')
                rows[sid] = [epoch(stamp), price if numeric else 0,
                             liq if numeric else 0, valid, sid]
        paths[mint] = sorted(rows.values(), key=lambda r: (r[0], r[4]))
    c.close()
    coverage = {day: dict(x, unique_tokens=len(coverage_tokens[day])) for day, x in sorted(days.items())}
    dataset = {'domain': DOMAIN, 'plan_hash': digest(plan), 'selected_rows_sha256': selected_hash.hexdigest(),
               'counts': dict(counts), 'coverage_by_day': coverage,
               'events': sorted(events, key=lambda e: (e['t'], -e['score'], e['id'])),
               'paths': paths}
    write_json(out / 'dataset.json', dataset)
    print(json.dumps({'phase': 'DATASET_FROZEN', 'counts': dict(counts), 'tokens': len(paths)}), flush=True)


def eligible(event, p, strict=False):
    return (not event['strict_static' if strict else 'diagnostic_static']
            and event['liq'] >= p.liquidity and event['buy'] >= p.buy_ratio
            and p.momentum <= event['momentum'] <= p.momentum_max
            and event['age'] <= p.age and event['score'] >= p.score
            and event['volume_ratio'] <= p.volume_ratio)


def first_fill(path, times, target, max_wait=WAIT):
    i = bisect_left(times, target)
    for j in range(i, len(path)):
        r = path[j]
        if r[0] > target+max_wait:
            break
        if r[3] and r[1] > 0 and r[2] >= 1000 and all(math.isfinite(x) for x in r[:3]):
            return j
    return None


def liquidation(row, quantity, stake, extra):
    slip = min(.05, .0035 + stake/(2*max(1, row[2])))
    return quantity*row[1]*(1-slip)*(1-.013-extra)-.12


def simulate(event, p, path, cash, extra=0):
    times = [r[0] for r in path]
    index = first_fill(path, times, event['t']+DELAY, max_wait=WAIT-DELAY)
    if index is None:
        return None
    entry = path[index]
    # Preserve the profile's entry liquidity requirement at the delayed price.
    if entry[2] < p.liquidity:
        return None
    stake = cash*p.fraction
    invested = stake*(1+.013+extra)+.12
    slip = min(.05, .0035+stake/(2*entry[2]))
    quantity = stake/(entry[1]*(1+slip))
    deadline, last, peak = entry[0]+p.hold, entry[0], 0.0
    marks = [(entry[0], liquidation(entry, quantity, stake, extra)-invested)]
    decision, reason = deadline, 'maximum_hold'
    unvalued = False
    for row in path[index+1:]:
        if row[0] > min(deadline, last+GAP):
            if last+GAP < deadline:
                decision, reason, unvalued = last+GAP, 'observation_gap_total_loss', True
            break
        if not row[3]:
            continue
        last = row[0]
        if row[1] <= 0 or row[2] < 1000:
            decision, reason, unvalued = row[0], 'unliquidatable_total_loss', True
            break
        value = liquidation(row, quantity, stake, extra)
        peak = max(peak, value)
        marks.append((row[0], value-invested))
        if value <= invested*(1-p.stop/100):
            decision, reason = row[0], 'stop_loss'; break
        if value >= invested*(1+p.take/100):
            decision, reason = row[0], 'take_profit'; break
        if peak >= invested*(1+p.trailing_arm/100) and value <= peak*(1-p.trailing_stop/100):
            decision, reason = row[0], 'trailing_stop'; break
    else:
        if last+GAP < deadline:
            decision, reason, unvalued = last+GAP, 'missing_followup_total_loss', True
    exit_index = None if unvalued else first_fill(path, times, decision+DELAY)
    if exit_index is None:
        pnl, exit_at = -invested, decision+DELAY+WAIT
        if not unvalued:
            reason = 'missing_exit_total_loss'
        unvalued = True
    else:
        exit_at = path[exit_index][0]
        pnl = liquidation(path[exit_index], quantity, stake, extra)-invested
    # An exit decision does not close the position. Include observed equity
    # during the pending sell delay/wait, without changing the frozen decision.
    for row in path[index+1:]:
        if row[0] > exit_at:
            break
        if row[0] <= marks[-1][0] or not row[3]:
            continue
        value = 0 if row[1] <= 0 or row[2] < 1000 else liquidation(row, quantity, stake, extra)
        marks.append((row[0], value-invested))
    marks.append((exit_at, pnl))
    return {'mint': event['mint'], 'snapshot_id': event['id'], 'entry_at': entry[0],
            'exit_at': exit_at, 'decision_at': event['t'], 'pnl': pnl,
            'stake': stake, 'invested': invested, 'reason': reason,
            'unvalued_total_loss': unvalued, 'marks': marks}


def metrics(trades, initial=1000, missed=0):
    gains = sum(max(t['pnl'], 0) for t in trades)
    losses = -sum(min(t['pnl'], 0) for t in trades)
    peak = equity = initial; dd = 0
    for t in trades:
        for _, unrealized in t['marks']:
            marked = equity+unrealized
            peak = max(peak, marked)
            dd = max(dd, (peak-marked)/peak*100)
        equity += t['pnl']
    top = sum(sorted((max(t['pnl'], 0) for t in trades), reverse=True)[:3])
    return {'trades': len(trades), 'unique_tokens': len({t['mint'] for t in trades}),
            'entry_days': len({iso(t['entry_at'])[:10] for t in trades}),
            'win_rate_pct': 100*sum(t['pnl']>0 for t in trades)/len(trades) if trades else None,
            'pf': gains/losses if losses else None, 'net_usd': equity-initial,
            'return_pct': (equity/initial-1)*100, 'drawdown_pct': dd,
            'top3_share_of_gross_profit_pct': 100*top/gains if gains else None,
            'top3_share_of_net_profit_pct': 100*top/(equity-initial) if equity>initial else None,
            'net_without_top3': equity-initial-top,
            'unvalued_total_losses': sum(t['unvalued_total_loss'] for t in trades),
            'missing_entry_prices': missed,
            'mean_pnl_usd': (equity-initial)/len(trades) if trades else None}


def portfolio(events, paths, p, start, end, extra=0, strict=False):
    cash, available, traded, trades, missed = 1000.0, start, set(), [], 0
    for event in events:
        if not start <= event['t'] < end-PURGE or event['t'] < available:
            continue
        if event['mint'] in traded or not eligible(event, p, strict):
            continue
        trade = simulate(event, p, paths[event['mint']], cash, extra)
        if trade is None:
            missed += 1; continue
        if trade['exit_at'] >= end:
            raise ValueError('split purge failed')
        cash += trade['pnl']; available = trade['exit_at']; traded.add(event['mint']); trades.append(trade)
        if cash <= 1:
            break
    return metrics(trades, missed=missed), trades


def admissible(train, val, plan):
    return (train['trades'] >= plan['minimum_trades_train']
            and val['trades'] >= plan['minimum_trades_validation']
            and val['unique_tokens'] >= plan['minimum_unique_tokens_validation']
            and train['net_usd'] > 0 and val['net_usd'] > 0
            and (train['pf'] or 0) >= 1.2 and (val['pf'] or 0) >= 1.2)


def final_qualifies(m, uncertainty, heavy, plan):
    bounds = uncertainty.get('mean_pnl_95pct', [-math.inf, math.inf])
    return (m['trades'] >= plan['minimum_trades_final_test']
            and m['unique_tokens'] >= plan['minimum_trades_final_test']
            and m['entry_days'] >= plan['minimum_test_days']
            and (m['win_rate_pct'] or 0) >= 70 and (m['pf'] or 0) >= 2
            and m['drawdown_pct'] <= 15
            and m['top3_share_of_net_profit_pct'] is not None
            and m['top3_share_of_net_profit_pct'] <= 25
            and m['net_without_top3'] > 0 and bounds[0] > 0
            and heavy['net_usd'] > 0)


def bootstrap(trades, repeats=1000):
    by_day = defaultdict(list)
    for t in trades:
        by_day[iso(t['entry_at'])[:10]].append(t['pnl'])
    if len(by_day) < 3:
        return {'status': 'INSUFFICIENT_INDEPENDENT_DAYS', 'days': len(by_day)}
    blocks = list(by_day.values()); rng = random.Random(20261006); values = []
    for _ in range(repeats):
        sample = [v for _ in blocks for v in rng.choice(blocks)]
        values.append(sum(sample)/len(sample))
    values.sort()
    return {'status': 'DESCRIPTIVE_BLOCK_BOOTSTRAP_NOT_MULTIPLE_TEST_CORRECTED',
            'days': len(blocks), 'mean_pnl_95pct': [values[int(.025*repeats)], values[int(.975*repeats)]]}


def study(out):
    out = Path(out); plan = json.loads((out/'plan.json').read_text()); verify_plan(plan)
    data = json.loads((out/'dataset.json').read_text())
    if data['plan_hash'] != digest(plan):
        raise ValueError('dataset/plan mismatch')
    if (out/'selection.json').exists() or (out/'results.json').exists():
        raise ValueError('final test is single-use; results already exist')
    bounds = {k: tuple(epoch(t) for t in x) for k, x in plan['splits'].items()}
    profiles = [Parameters(**x) for x in plan['grid']]
    trainval = []
    for p in profiles:
        result = {'parameters': asdict(p)}
        for split in ('train', 'validation'):
            result[split] = {}
            for cost in plan['extra_cost_per_side']:
                m, _ = portfolio(data['events'], data['paths'], p, *bounds[split], extra=cost)
                result[split][str(cost)] = m
        tr, va = result['train']['0.01'], result['validation']['0.01']
        result['admissible'] = admissible(tr, va, plan)
        result['selection_score'] = min(tr['mean_pnl_usd'] or -1e9, va['mean_pnl_usd'] or -1e9)
        trainval.append(result)
    ranked = sorted(trainval, key=lambda x: (x['admissible'], x['selection_score'], x['parameters']['name']), reverse=True)
    selected = ranked[0]['parameters'] if ranked[0]['admissible'] else None
    exploratory = ranked[0]['parameters']
    selection = {'domain': DOMAIN, 'plan_hash': digest(plan),
                 'dataset_sha256': hashlib.sha256((out/'dataset.json').read_bytes()).hexdigest(),
                 'selected': selected, 'exploratory_leader': exploratory,
                 'status': 'CONDITIONAL_PROXY_CANDIDATE' if selected else 'ABSTAIN_INSUFFICIENT_OR_UNPROFITABLE',
                 'frozen_before_final_test': True, 'train_validation': ranked}
    write_json(out/'selection.json', selection)
    # Final test never influences ranking or a second choice. Baseline and a
    # pre-frozen exploratory leader are descriptive comparisons if selection abstains.
    baseline = next(p for p in profiles if p.name == 'baseline/current/0.1')
    cases = {'baseline': baseline, 'frozen_candidate' if selected else 'frozen_exploratory_leader': Parameters(**(selected or exploratory))}
    final = {}; trade_evidence = {}
    for label, p in cases.items():
        final[label] = {}
        for cost in plan['extra_cost_per_side']:
            m, trades = portfolio(data['events'], data['paths'], p, *bounds['test'], extra=cost)
            final[label][str(cost)] = m
            if cost == .01:
                final[label]['uncertainty'] = bootstrap(trades)
                trade_evidence[label] = trades
    strict = {}
    for split, times in bounds.items():
        strict[split] = {p.name: portfolio(data['events'], data['paths'], p, *times, strict=True)[0]['trades'] for p in profiles}
    candidate_final = final.get('frozen_candidate')
    qualified = bool(candidate_final and final_qualifies(candidate_final['0.01'],
                     candidate_final['uncertainty'], candidate_final['0.03'], plan))
    result = {'domain': DOMAIN, 'plan_hash': digest(plan), 'selection': selection['status'],
              'outcome': 'CONDITIONAL_RESEARCH_CANDIDATE_ONLY' if qualified else 'NO_VALIDATED_PARAMETERS',
              'conditional_final_test_qualified': qualified,
              'selected': selected, 'exploratory_leader': exploratory, 'final_test': final,
              'strict_security_lane_trade_counts': strict, 'counts': data['counts'],
              'coverage_by_day': data['coverage_by_day'], 'final_trade_evidence': trade_evidence,
              'live_ready': False, 'broadcast': False, 'signing': False,
              'profitability_proven': False, 'source_rows_sha256': data['selected_rows_sha256'],
              'constraints': plan['caveats'], 'configs_tested': len(profiles)}
    write_json(out/'results.json', result)
    print(json.dumps({'phase': 'STUDY_DONE', 'selection': result['selection'],
                      'selected': selected, 'exploratory_leader': exploratory, 'final_test': final}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('plan', 'extract', 'study'))
    parser.add_argument('--out', required=True)
    parser.add_argument('--source', default=str(ROOT/'CORE/data/imc-main.db'))
    args = parser.parse_args()
    {'plan': lambda: make_plan(args.source, args.out),
     'extract': lambda: extract(args.out), 'study': lambda: study(args.out)}[args.phase]()


if __name__ == '__main__':
    main()
