"""Maturity-aware online batches and an OS-released exclusive training lock."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from .data import make_records, split_records
from .exit_training import augment_exit_records


@contextmanager
def training_lock(runtime):
    path=Path(runtime)/'training.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    try:
        if path.stat().st_size==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if __import__('os').name=='nt':
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise BlockingIOError('another learner owns training.lock') from exc
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if __import__('os').name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    finally:stream.close()


def prepare_online_batch(groups, *, minimum_observation_span=180):
    research={c:rows for c,rows in groups.items() if int(hashlib.sha256(c.encode()).hexdigest()[:8],16)%5!=0}
    records=[]
    eligible=set()
    for coin,rows in research.items():
        if not rows or max(r['ts'] for r in rows)-min(r['ts'] for r in rows)<minimum_observation_span:
            continue
        latest=max(r['ts'] for r in rows)
        # All requested horizons had time to mature; missing measurements remain masked.
        mature=[r for r in make_records(rows,max_per_coin=24) if r['observation']['ts']+121<=latest]
        if mature:records.extend(mature);eligible.add(coin)
    splits,_=split_records(records)
    consumed={r['coin_id'] for values in splits.values() for r in values}
    return {'splits':splits,'training_records':augment_exit_records(splits['train'],research),
        'consumed_coins':consumed,'new_coins':len(research),'mature_coins':len(eligible),
        'immature_or_unlabelled_coins':sorted(set(research)-consumed),
        'reserved_forward_coins':sorted(set(groups)-set(research))}
