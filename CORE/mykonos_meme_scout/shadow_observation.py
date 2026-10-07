"""Read bound research picks; observe their markets without entry authority."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
import math


def bound_targets(path, *, observed_at=None, limit=30):
    """No migration, inferred pair, or historical resampling on this read path."""
    if path is None or not path.exists():
        return [], []
    observed_at = observed_at or datetime.now(timezone.utc)
    targets, errors = [], []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as c:
        c.execute('PRAGMA query_only=ON')
        columns = {r[1] for r in c.execute('PRAGMA table_info(picks)')}
        if 'pair_address' not in columns:
            return [], ['shadow_pair_schema_missing']
        rows = c.execute("SELECT address,pair_address,entry_at FROM picks "
                         "WHERE variant='SAFE100' AND accepted=1 AND status='OPEN' "
                         "ORDER BY entry_at DESC LIMIT ?", (limit+1,)).fetchall()
    for mint, pair, entered in rows:
        try:
            age = (observed_at-datetime.fromisoformat(entered.replace('Z','+00:00'))).total_seconds()
        except (ValueError, TypeError, AttributeError):
            errors.append('shadow_entry_time_invalid')
            continue
        if not 0 <= age <= 600:
            continue
        if not isinstance(mint, str) or not mint or not isinstance(pair, str) or not pair:
            errors.append('shadow_pair_binding_missing')
            continue
        if len(targets) == limit:
            errors.append('shadow_watch_capacity')
            break
        targets.append((mint, pair))
    return targets, errors


def valid_observation(snapshot, expected_pair, *, observed_at=None):
    observed_at = observed_at or datetime.now(timezone.utc)
    try:
        age = (observed_at-datetime.fromisoformat(snapshot.captured_at.replace('Z','+00:00'))).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return False
    if (snapshot.pair_address != expected_pair or snapshot.market_data_stale
            or not math.isfinite(snapshot.price_usd) or not snapshot.price_usd > 0
            or not 0 <= age <= 10):
        return False
    return True


def record_observation(store, snapshot, expected_pair, *, observed_at=None):
    if not valid_observation(snapshot, expected_pair, observed_at=observed_at):
        return False
    previous = store.latest_snapshot(snapshot.address)
    # Market refresh carries original security provenance, never a new stamp.
    if previous and previous.pair_address == expected_pair and not snapshot.security.checked:
        snapshot = replace(snapshot, security=previous.security)
    store.record_snapshot(snapshot)
    return True
