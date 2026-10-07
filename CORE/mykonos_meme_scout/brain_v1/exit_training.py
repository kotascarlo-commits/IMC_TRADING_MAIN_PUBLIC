"""Causal held-position examples; augmentation never creates independent coins."""
from bisect import bisect_right
from copy import deepcopy
from .data import FEATURE_NAMES, timestamp


def augment_exit_records(records, observations_by_coin, holding_seconds=(30,60,120)):
    result=list(records)
    for record in records:
        if record['targets'].get('exit_now') is None:
            continue
        rows=observations_by_coin.get(record['coin_id'],[])
        cutoff=timestamp(record['observed_at'])
        past=sorted((r for r in rows if r['ts']<=cutoff and r.get('pair_address','')==record['observation'].get('pair_address','')),key=lambda r:r['ts'])
        times=[r['ts'] for r in past]
        for held in holding_seconds:
            idx=bisect_right(times,cutoff-held)-1
            if idx<0 or cutoff-held-times[idx]>3:
                continue
            entry=past[idx]
            trajectory=past[idx:]
            if entry['price_usd']<=0 or any(r['market_data_stale'] for r in trajectory) or any(b['ts']-a['ts']>30 for a,b in zip(trajectory,trajectory[1:])):
                continue
            peak=max(r['price_usd'] for r in trajectory)
            price=record['observation']['price_usd']
            values={'position_gain':(price/entry['price_usd']-1)*100,
                'position_peak_gain':(peak/entry['price_usd']-1)*100,
                'position_giveback':(peak-price)/entry['price_usd']*100,
                'position_elapsed_seconds':cutoff-entry['ts']}
            example=deepcopy(record)
            for name,value in values.items():example['sequence'][-1][FEATURE_NAMES.index(name)]=value
            # Exit-only supervision: extra position contexts cannot multiply entry labels.
            example['targets']={k:v if k in ('exit_now','continuation') else None for k,v in record['targets'].items()}
            example['position_entry_ts']=entry['ts']
            example['position_known_at_ts']=cutoff
            result.append(example)
    return result
