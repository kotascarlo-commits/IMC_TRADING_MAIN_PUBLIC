from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .sqlite_source import readonly_connection as _readonly_connection, verified_source
from .compact_storage import logical_table_names


SIMULATION_SCHEMA_VERSION = 2
SIMULATION_DATA_DOMAIN = "SIMULATION"
REAL_DATA_DOMAIN = "REAL_PAPER"
GENERATOR_VERSION = "0.6.0-simulation-engine"
SYNTHETIC_FAMILIES = (
    "rocket",
    "normal_scalper",
    "fakeout",
    "stagnation",
    "liquidity_shock",
    "rug",
    "crash_recovery",
    "social_flow_divergence",
)
SYNTHETIC_FAMILY_ALIASES = {"scalp": "normal_scalper"}
DEFAULT_HORIZONS_SECONDS = (60, 180, 300, 600, 900)


SIMULATION_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS simulation_metadata (
  id INTEGER PRIMARY KEY CHECK(id=1),
  schema_version INTEGER NOT NULL,
  data_domain TEXT NOT NULL CHECK(data_domain='SIMULATION'),
  eligible_for_neural_research INTEGER NOT NULL CHECK(eligible_for_neural_research=1),
  eligible_for_real_training INTEGER NOT NULL CHECK(eligible_for_real_training=0),
  eligible_for_real_champion INTEGER NOT NULL CHECK(eligible_for_real_champion=0),
  execution_authority_pct REAL NOT NULL CHECK(execution_authority_pct=0),
  created_at TEXT NOT NULL,
  generator_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulation_runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  seed INTEGER NOT NULL,
  scenario_count INTEGER NOT NULL,
  tick_count INTEGER NOT NULL,
  example_count INTEGER NOT NULL,
  config_json TEXT NOT NULL,
  run_checksum TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulation_scenarios (
  scenario_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
  origin TEXT NOT NULL CHECK(origin IN ('SYNTHETIC','HISTORICAL_RESAMPLE')),
  family TEXT NOT NULL,
  seed INTEGER NOT NULL,
  duration_seconds INTEGER NOT NULL,
  source_fingerprint TEXT,
  sequence_checksum TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_simulation_scenarios_run
  ON simulation_scenarios(run_id, origin, family);
CREATE TABLE IF NOT EXISTS simulation_ticks (
  scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(scenario_id),
  second_offset INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  price_usd REAL NOT NULL CHECK(price_usd>0),
  liquidity_usd REAL NOT NULL CHECK(liquidity_usd>=0),
  market_cap_usd REAL NOT NULL CHECK(market_cap_usd>=0),
  volume_1s_usd REAL NOT NULL CHECK(volume_1s_usd>=0),
  buys_1s INTEGER NOT NULL CHECK(buys_1s>=0),
  sells_1s INTEGER NOT NULL CHECK(sells_1s>=0),
  cumulative_volume_usd REAL NOT NULL CHECK(cumulative_volume_usd>=0),
  social_mentions_1s INTEGER NOT NULL CHECK(social_mentions_1s>=0),
  social_sentiment REAL NOT NULL CHECK(social_sentiment BETWEEN -1 AND 1),
  smart_wallet_flow_1s_usd REAL NOT NULL,
  social_data_available INTEGER NOT NULL CHECK(social_data_available IN (0,1)),
  rug_event INTEGER NOT NULL CHECK(rug_event IN (0,1)),
  data_domain TEXT NOT NULL CHECK(data_domain='SIMULATION'),
  PRIMARY KEY(scenario_id, second_offset)
);
CREATE INDEX IF NOT EXISTS idx_simulation_ticks_time
  ON simulation_ticks(scenario_id, captured_at);
CREATE TABLE IF NOT EXISTS simulation_training_examples (
  example_id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(scenario_id),
  observed_second INTEGER NOT NULL,
  horizon_seconds INTEGER NOT NULL,
  data_domain TEXT NOT NULL CHECK(data_domain='SIMULATION'),
  eligible_for_neural_research INTEGER NOT NULL CHECK(eligible_for_neural_research=1),
  eligible_for_real_training INTEGER NOT NULL CHECK(eligible_for_real_training=0),
  eligible_for_real_champion INTEGER NOT NULL CHECK(eligible_for_real_champion=0),
  features_json TEXT NOT NULL,
  labels_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  UNIQUE(scenario_id, observed_second, horizon_seconds)
);
CREATE INDEX IF NOT EXISTS idx_simulation_examples_scenario
  ON simulation_training_examples(scenario_id, observed_second, horizon_seconds);

CREATE TRIGGER IF NOT EXISTS simulation_metadata_no_update
BEFORE UPDATE ON simulation_metadata BEGIN
  SELECT RAISE(ABORT, 'simulation metadata is immutable');
END;
CREATE TRIGGER IF NOT EXISTS simulation_metadata_no_delete
BEFORE DELETE ON simulation_metadata BEGIN
  SELECT RAISE(ABORT, 'simulation metadata is immutable');
END;
CREATE TRIGGER IF NOT EXISTS simulation_runs_no_update
BEFORE UPDATE ON simulation_runs BEGIN
  SELECT RAISE(ABORT, 'simulation runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_runs_no_delete
BEFORE DELETE ON simulation_runs BEGIN
  SELECT RAISE(ABORT, 'simulation runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_scenarios_no_update
BEFORE UPDATE ON simulation_scenarios BEGIN
  SELECT RAISE(ABORT, 'simulation scenarios are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_scenarios_no_delete
BEFORE DELETE ON simulation_scenarios BEGIN
  SELECT RAISE(ABORT, 'simulation scenarios are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_ticks_no_update
BEFORE UPDATE ON simulation_ticks BEGIN
  SELECT RAISE(ABORT, 'simulation ticks are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_ticks_no_delete
BEFORE DELETE ON simulation_ticks BEGIN
  SELECT RAISE(ABORT, 'simulation ticks are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_examples_no_update
BEFORE UPDATE ON simulation_training_examples BEGIN
  SELECT RAISE(ABORT, 'simulation examples are append-only');
END;
CREATE TRIGGER IF NOT EXISTS simulation_examples_no_delete
BEFORE DELETE ON simulation_training_examples BEGIN
  SELECT RAISE(ABORT, 'simulation examples are append-only');
END;
"""


@dataclass(frozen=True, slots=True)
class SequenceTick:
    second_offset: int
    captured_at: str
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_1s_usd: float
    buys_1s: int
    sells_1s: int
    cumulative_volume_usd: float
    rug_event: bool = False
    data_domain: str = SIMULATION_DATA_DOMAIN
    social_mentions_1s: int = 0
    social_sentiment: float = 0.0
    smart_wallet_flow_1s_usd: float = 0.0
    social_data_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScenarioPath:
    scenario_id: str
    origin: str
    family: str
    seed: int
    ticks: tuple[SequenceTick, ...]
    metadata: dict[str, Any]
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.origin not in {"SYNTHETIC", "HISTORICAL_RESAMPLE"}:
            raise ValueError(f"Unknown scenario origin: {self.origin}")
        if len(self.ticks) < 2:
            raise ValueError("A scenario needs at least two sequence ticks")
        for expected_second, tick in enumerate(self.ticks):
            if tick.second_offset != expected_second:
                raise ValueError("Sequence ticks must be contiguous and start at second zero")
            if tick.data_domain != SIMULATION_DATA_DOMAIN:
                raise ValueError("Every sequence tick must be marked SIMULATION")
            if tick.price_usd <= 0 or tick.liquidity_usd < 0 or tick.market_cap_usd < 0:
                raise ValueError("Sequence price/liquidity/market cap values are invalid")
            if tick.social_mentions_1s < 0 or not -1.0 <= tick.social_sentiment <= 1.0:
                raise ValueError("Sequence social-signal values are invalid")

    @property
    def duration_seconds(self) -> int:
        return self.ticks[-1].second_offset

    @property
    def checksum(self) -> str:
        digest = hashlib.sha256()
        for tick in self.ticks:
            digest.update(json.dumps(
                tick.as_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8"))
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class HistoricalObservation:
    captured_at: datetime
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_rate_per_second: float
    transaction_rate_per_second: float
    buy_ratio: float
    social_mentions_rate_per_second: float = 0.0
    social_sentiment: float = 0.0
    smart_wallet_flow_rate_per_second: float = 0.0
    social_data_available: bool = False


@dataclass(frozen=True, slots=True)
class HistoricalSequence:
    address_hash: str
    symbol: str
    source_database_sha256: str
    observations: tuple[HistoricalObservation, ...]
    source_integrity: dict[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scenario_id(origin: str, family: str, seed: int, index: int, source: str = "") -> str:
    raw = f"{GENERATOR_VERSION}|{origin}|{family}|{seed}|{index}|{source}"
    reproducible_prefix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    # Checksums, not database identity, carry reproducibility. The UUID suffix
    # lets an append-only database retain repeated runs with the same seed.
    return f"sim-{reproducible_prefix}-{uuid.uuid4().hex[:16]}"


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _interpolate(anchors: Sequence[tuple[float, float]], progress: float) -> float:
    progress = _clamp(progress, 0.0, 1.0)
    for index in range(1, len(anchors)):
        left_x, left_y = anchors[index - 1]
        right_x, right_y = anchors[index]
        if progress <= right_x:
            width = max(1e-12, right_x - left_x)
            weight = (progress - left_x) / width
            # Smoothstep removes artificial slope discontinuities at phase edges.
            weight = weight * weight * (3.0 - 2.0 * weight)
            return left_y + (right_y - left_y) * weight
    return anchors[-1][1]


def _synthetic_shape(family: str, event_progress: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]], float, float]:
    if family == "rocket":
        return (
            [(0, 0), (.08, 1.5), (.22, 7), (.38, 16), (.57, 29), (.78, 48), (1, 72)],
            [(0, 1), (.3, 1.08), (.65, 1.24), (1, 1.38)], .0018, .76,
        )
    if family == "normal_scalper":
        return (
            [(0, 0), (.10, -1.5), (.24, 10), (.38, 4), (.56, 17), (.72, 9), (.88, 23), (1, 15)],
            [(0, 1), (.25, 1.06), (.5, 1.02), (.75, 1.08), (1, 1.03)], .0027, .60,
        )
    if family == "fakeout":
        return (
            [(0, 0), (.08, 3), (.18, 13), (.28, 22), (.38, 15), (.55, 2), (.75, -10), (1, -24)],
            [(0, 1), (.25, 1.12), (.45, .98), (.72, .72), (1, .58)], .0025, .42,
        )
    if family == "rug":
        before = max(.05, event_progress - .015)
        after = min(.99, event_progress + .008)
        return (
            [(0, 0), (.18, 4), (.34, 14), (before, 9), (after, -84), (1, -94)],
            [(0, 1), (.35, 1.04), (before, .96), (after, .08), (1, .025)], .0019, .48,
        )
    if family == "stagnation":
        return (
            [(0, 0), (.18, .7), (.36, -.8), (.55, .9), (.74, -.5), (1, .4)],
            [(0, 1), (.3, .995), (.65, 1.01), (1, .99)], .00045, .51,
        )
    if family == "liquidity_shock":
        before = max(.08, event_progress - .02)
        after = min(.92, event_progress + .012)
        return (
            [(0, 0), (.18, 5), (.34, 11), (before, 8), (after, -18), (.74, -12), (1, -6)],
            [(0, 1), (.32, 1.06), (before, 1.02), (after, .28), (.72, .36), (1, .52)], .0024, .44,
        )
    if family == "crash_recovery":
        before = max(.10, event_progress - .035)
        after = min(.80, event_progress + .025)
        return (
            [(0, 0), (.18, 8), (.32, 16), (before, 10), (after, -36), (.68, -18), (.84, 7), (1, 19)],
            [(0, 1), (.35, 1.08), (before, .96), (after, .46), (.72, .70), (1, .94)], .0031, .57,
        )
    if family == "social_flow_divergence":
        return (
            [(0, 0), (.16, 4), (.32, 13), (.48, 18), (.64, 8), (.80, -7), (1, -19)],
            [(0, 1), (.32, 1.04), (.55, .94), (.78, .70), (1, .61)], .0025, .43,
        )
    raise ValueError(f"Unknown synthetic family: {family}")


def _synthetic_social_signals(
    family: str,
    progress: float,
    turnover: float,
    rng: random.Random,
) -> tuple[int, float, float]:
    """Return causal social attention, sentiment and signed smart-wallet flow."""
    base_mentions = {
        "rocket": 4.5,
        "normal_scalper": 3.0,
        "fakeout": 5.0,
        "stagnation": .25,
        "liquidity_shock": 2.0,
        "rug": 4.0,
        "crash_recovery": 3.5,
        "social_flow_divergence": 12.0,
    }[family]
    attention_curve = .35 + 1.65 * progress
    if family == "social_flow_divergence":
        attention_curve = .7 + 4.8 * _interpolate(((0, .1), (.25, .5), (.55, 1), (1, .82)), progress)
    expected_mentions = base_mentions * attention_curve
    mentions = max(0, int(round(rng.gauss(expected_mentions, math.sqrt(max(.05, expected_mentions))))))

    sentiment = {
        "rocket": .72,
        "normal_scalper": .48,
        "fakeout": .70 if progress < .48 else .42,
        "stagnation": .04,
        "liquidity_shock": .20 if progress < .45 else -.35,
        "rug": .55 if progress < .45 else -.82,
        "crash_recovery": .38 if progress < .40 else (-.55 if progress < .66 else .32),
        "social_flow_divergence": .82,
    }[family]
    sentiment = _clamp(sentiment + rng.gauss(0, .07), -1.0, 1.0)
    wallet_bias = {
        "rocket": .34,
        "normal_scalper": .08,
        "fakeout": .14 if progress < .42 else -.38,
        "stagnation": 0.0,
        "liquidity_shock": .03 if progress < .42 else -.62,
        "rug": .06 if progress < .40 else -.90,
        "crash_recovery": .10 if progress < .40 else (-.52 if progress < .68 else .28),
        "social_flow_divergence": -.72,
    }[family]
    smart_wallet_flow = turnover * _clamp(wallet_bias + rng.gauss(0, .08), -1.0, 1.0)
    return mentions, sentiment, smart_wallet_flow


def generate_synthetic_path(
    family: str,
    *,
    duration_seconds: int = 900,
    seed: int = 20260904,
    index: int = 0,
) -> ScenarioPath:
    """Create one reproducible, contiguous one-second synthetic launch path."""
    family = family.lower().strip()
    family = SYNTHETIC_FAMILY_ALIASES.get(family, family)
    if family not in SYNTHETIC_FAMILIES:
        raise ValueError(f"family must be one of {', '.join(SYNTHETIC_FAMILIES)}")
    if not 60 <= int(duration_seconds) <= 86_400:
        raise ValueError("duration_seconds must be between 60 and 86400")

    duration_seconds = int(duration_seconds)
    scenario_seed = int(seed) + index * 10_007 + SYNTHETIC_FAMILIES.index(family) * 1_000_003
    rng = random.Random(scenario_seed)
    event_progress = rng.uniform(.42, .62)
    return_anchors, liquidity_anchors, noise_sigma, base_buy_ratio = _synthetic_shape(
        family, event_progress
    )
    event_second = int(round(event_progress * duration_seconds))
    rug_second = event_second if family == "rug" else None
    start_price = 10 ** rng.uniform(-5.2, -2.5)
    base_liquidity = rng.uniform(18_000, 95_000)
    initial_mc_multiple = rng.uniform(2.8, 6.5)
    base_market_cap = base_liquidity * initial_mc_multiple
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=(abs(scenario_seed) % (120 * 24 * 3600))
    )

    ticks: list[SequenceTick] = []
    cumulative_volume = 0.0
    micro_state = 0.0
    previous_price = start_price
    for second in range(duration_seconds + 1):
        progress = second / duration_seconds
        if second == 0:
            micro_state = 0.0
            micro_noise = 0.0
        else:
            micro_state = .72 * micro_state + rng.gauss(0.0, noise_sigma)
            micro_noise = micro_state + rng.gauss(0.0, noise_sigma * .28)
        target_return = _interpolate(return_anchors, progress)
        target_log_factor = math.log(max(.001, 1.0 + target_return / 100.0))
        price = start_price * math.exp(target_log_factor + micro_noise)
        liquidity_multiplier = max(.001, _interpolate(liquidity_anchors, progress))
        liquidity = base_liquidity * liquidity_multiplier * math.exp(micro_noise * .20)
        if family == "rug" and rug_second is not None and second >= rug_second:
            # The discontinuity is intentional and cannot be smoothed away by noise.
            post_rug = max(0, second - rug_second)
            price_cap = previous_price * (.11 if post_rug == 0 else 1.0)
            price = min(price, price_cap)
            liquidity = min(liquidity, base_liquidity * (.10 if post_rug == 0 else .10))
        market_cap = base_market_cap * (price / start_price)

        move = 0.0 if second == 0 else price / previous_price - 1.0
        burst = min(4.0, abs(move) * 85.0)
        family_activity = {
            "rocket": 1.55, "normal_scalper": 1.35, "fakeout": 1.20,
            "rug": .95 if rug_second is None or second < rug_second else .18,
            "stagnation": .22,
            "liquidity_shock": 1.10 if second < event_second else .55,
            "crash_recovery": 1.30,
            "social_flow_divergence": 1.45,
        }[family]
        expected_transactions = max(.05, family_activity * (1.5 + 2.5 * progress + burst))
        transaction_count = max(0, int(round(rng.gauss(expected_transactions, math.sqrt(expected_transactions)))))
        directional_bias = .18 if move > 0 else (-.18 if move < 0 else 0.0)
        if family == "rug" and rug_second is not None and second >= rug_second:
            buy_ratio = .05
        elif family == "liquidity_shock" and second >= event_second:
            buy_ratio = .20
        elif family == "crash_recovery" and event_second <= second < int(duration_seconds * .70):
            buy_ratio = .24
        elif family == "crash_recovery" and second >= int(duration_seconds * .70):
            buy_ratio = .72
        elif family == "social_flow_divergence":
            buy_ratio = _clamp(.58 - .34 * progress + rng.gauss(0, .04), .08, .90)
        else:
            buy_ratio = _clamp(base_buy_ratio + directional_bias + rng.gauss(0, .06), .03, .97)
        buys = sum(1 for _ in range(transaction_count) if rng.random() < buy_ratio)
        sells = transaction_count - buys
        turnover = family_activity * base_liquidity * rng.uniform(.00012, .00045) * (1 + burst)
        if second == 0:
            turnover = 0.0
            buys = sells = 0
            social_mentions = 0
            social_sentiment = 0.0
            smart_wallet_flow = 0.0
        else:
            social_mentions, social_sentiment, smart_wallet_flow = _synthetic_social_signals(
                family, progress, turnover, rng
            )
        cumulative_volume += turnover
        ticks.append(SequenceTick(
            second_offset=second,
            captured_at=(start_time + timedelta(seconds=second)).isoformat(),
            price_usd=price,
            liquidity_usd=max(0.0, liquidity),
            market_cap_usd=max(0.0, market_cap),
            volume_1s_usd=max(0.0, turnover),
            buys_1s=buys,
            sells_1s=sells,
            cumulative_volume_usd=cumulative_volume,
            social_mentions_1s=social_mentions,
            social_sentiment=social_sentiment,
            smart_wallet_flow_1s_usd=smart_wallet_flow,
            social_data_available=True,
            rug_event=bool(rug_second is not None and second == rug_second),
        ))
        previous_price = price

    scenario_id = _scenario_id("SYNTHETIC", family, scenario_seed, index)
    return ScenarioPath(
        scenario_id=scenario_id,
        origin="SYNTHETIC",
        family=family,
        seed=scenario_seed,
        ticks=tuple(ticks),
        metadata={
            "generator_version": GENERATOR_VERSION,
            "archetype": family,
            "duration_seconds": duration_seconds,
            "rug_second": rug_second,
            "event_second": event_second if family in {"liquidity_shock", "crash_recovery"} else None,
            "one_second_cadence": True,
            "social_flow_signals": True,
            "data_domain": SIMULATION_DATA_DOMAIN,
            "eligible_for_neural_research": True,
            "eligible_for_real_training": False,
            "eligible_for_real_champion": False,
        },
    )


def load_historical_sequences(
    source_database: Path,
    *,
    minimum_observations: int = 3,
    maximum_sequences: int = 500,
) -> list[HistoricalSequence]:
    """Extract a verified idle main/WAL source; SQLite may update SHM bookkeeping."""
    source_database = Path(source_database).resolve()
    if not source_database.is_file():
        raise FileNotFoundError(f"Historical source database not found: {source_database}")
    with verified_source(source_database) as (connection, integrity):
        tables = logical_table_names(connection)
        if "simulation_metadata" in tables:
            raise ValueError("A simulation database cannot be used as a historical real-data source")
        if "snapshots" not in tables:
            raise ValueError("Historical source does not contain the real snapshots table")
        addresses = connection.execute(
            """SELECT address,COUNT(*) AS n FROM snapshots
            GROUP BY address HAVING COUNT(*)>=? ORDER BY MIN(captured_at) LIMIT ?""",
            (max(2, int(minimum_observations)), max(1, int(maximum_sequences))),
        ).fetchall()
        sequences: list[HistoricalSequence] = []
        for address_row in addresses:
            address = str(address_row["address"])
            rows = connection.execute(
                """SELECT captured_at,symbol,price_usd,payload_json FROM snapshots
                WHERE address=? ORDER BY captured_at,id""",
                (address,),
            ).fetchall()
            observations: list[HistoricalObservation] = []
            seen_times: set[datetime] = set()
            symbol = "UNKNOWN"
            for row in rows:
                try:
                    captured = _parse_time(str(row["captured_at"]))
                    payload = json.loads(str(row["payload_json"]))
                    price = float(row["price_usd"])
                    liquidity = float(payload.get("liquidity_usd") or 0.0)
                    market_cap = float(payload.get("market_cap_usd") or 0.0)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if captured in seen_times or price <= 0 or liquidity < 0 or market_cap < 0:
                    continue
                seen_times.add(captured)
                symbol = str(row["symbol"] or payload.get("symbol") or "UNKNOWN")
                created_ms = int(payload.get("pair_created_at_ms") or 0)
                if created_ms > 0:
                    observed_age = (captured.timestamp() * 1000 - created_ms) / 60_000
                    age_minutes = max(.33, min(5.0, observed_age))
                else:
                    age_minutes = 5.0
                volume_rate = float(payload.get("volume_5m_usd") or 0.0) / (age_minutes * 60.0)
                buys = max(0, int(payload.get("buys_5m") or 0))
                sells = max(0, int(payload.get("sells_5m") or 0))
                total = buys + sells
                social_mentions = max(0.0, float(payload.get("social_mentions_5m") or 0.0))
                social_sentiment_raw = payload.get("social_sentiment")
                smart_wallet_flow = float(payload.get("smart_wallet_net_flow_5m_usd") or 0.0)
                social_available = any(
                    key in payload for key in (
                        "social_mentions_5m", "social_sentiment", "smart_wallet_net_flow_5m_usd"
                    )
                )
                observations.append(HistoricalObservation(
                    captured_at=captured,
                    price_usd=price,
                    liquidity_usd=liquidity,
                    market_cap_usd=market_cap,
                    volume_rate_per_second=max(0.0, volume_rate),
                    transaction_rate_per_second=total / (age_minutes * 60.0),
                    buy_ratio=buys / total if total else .5,
                    social_mentions_rate_per_second=social_mentions / (age_minutes * 60.0),
                    social_sentiment=_clamp(float(social_sentiment_raw or 0.0), -1.0, 1.0),
                    smart_wallet_flow_rate_per_second=smart_wallet_flow / (age_minutes * 60.0),
                    social_data_available=social_available,
                ))
            if len(observations) >= max(2, int(minimum_observations)):
                sequences.append(HistoricalSequence(
                    address_hash=hashlib.sha256(address.encode("utf-8")).hexdigest(),
                    symbol=symbol,
                    source_database_sha256=integrity["sha256"],
                    observations=tuple(observations),
                    source_integrity=integrity,
                ))
    return sequences


def _interpolate_history(sequence: HistoricalSequence) -> list[HistoricalObservation]:
    observations = sequence.observations
    start = observations[0].captured_at
    end_second = int((observations[-1].captured_at - start).total_seconds())
    if end_second < 2:
        raise ValueError("Historical sequence does not span at least two seconds")
    result: list[HistoricalObservation] = []
    right = 1
    for second in range(end_second + 1):
        target = start + timedelta(seconds=second)
        while right < len(observations) - 1 and observations[right].captured_at < target:
            right += 1
        left_obs = observations[max(0, right - 1)]
        right_obs = observations[right]
        span = max(1e-9, (right_obs.captured_at - left_obs.captured_at).total_seconds())
        weight = _clamp((target - left_obs.captured_at).total_seconds() / span, 0.0, 1.0)

        def log_interp(left: float, right_value: float) -> float:
            if left <= 0 or right_value <= 0:
                return max(0.0, left + (right_value - left) * weight)
            return math.exp(math.log(left) + (math.log(right_value) - math.log(left)) * weight)

        result.append(HistoricalObservation(
            captured_at=target,
            price_usd=log_interp(left_obs.price_usd, right_obs.price_usd),
            liquidity_usd=log_interp(left_obs.liquidity_usd, right_obs.liquidity_usd),
            market_cap_usd=log_interp(left_obs.market_cap_usd, right_obs.market_cap_usd),
            volume_rate_per_second=max(0.0, left_obs.volume_rate_per_second + (
                right_obs.volume_rate_per_second - left_obs.volume_rate_per_second
            ) * weight),
            transaction_rate_per_second=max(0.0, left_obs.transaction_rate_per_second + (
                right_obs.transaction_rate_per_second - left_obs.transaction_rate_per_second
            ) * weight),
            buy_ratio=_clamp(left_obs.buy_ratio + (right_obs.buy_ratio - left_obs.buy_ratio) * weight, 0, 1),
            social_mentions_rate_per_second=max(0.0, left_obs.social_mentions_rate_per_second + (
                right_obs.social_mentions_rate_per_second - left_obs.social_mentions_rate_per_second
            ) * weight),
            social_sentiment=_clamp(left_obs.social_sentiment + (
                right_obs.social_sentiment - left_obs.social_sentiment
            ) * weight, -1, 1),
            smart_wallet_flow_rate_per_second=left_obs.smart_wallet_flow_rate_per_second + (
                right_obs.smart_wallet_flow_rate_per_second - left_obs.smart_wallet_flow_rate_per_second
            ) * weight,
            social_data_available=left_obs.social_data_available or right_obs.social_data_available,
        ))
    return result


def _classify_path(ticks: Sequence[SequenceTick]) -> str:
    entry = ticks[0].price_usd
    returns = [(tick.price_usd / entry - 1.0) * 100.0 for tick in ticks]
    liquidity_ratio = min(tick.liquidity_usd for tick in ticks) / max(ticks[0].liquidity_usd, 1e-12)
    if any(tick.rug_event for tick in ticks) or min(returns) <= -40 or liquidity_ratio <= .20:
        return "rug"
    if liquidity_ratio <= .35:
        return "liquidity_shock"
    if min(returns) <= -25 and returns[-1] - min(returns) >= 25 and returns[-1] > 0:
        return "crash_recovery"
    social_hype = sum(max(0.0, tick.social_sentiment) * tick.social_mentions_1s for tick in ticks)
    smart_flow = sum(tick.smart_wallet_flow_1s_usd for tick in ticks)
    if social_hype >= 100 and smart_flow < 0:
        return "social_flow_divergence"
    if max(returns) >= 50:
        return "rocket"
    if max(returns) >= 10 and (returns[-1] <= 0 or max(returns) - returns[-1] >= 20):
        return "fakeout"
    if max(returns) >= 10:
        return "normal_scalper"
    if max(returns) < 3 and min(returns) > -10:
        return "stagnation"
    return "historical_mixed"


def resample_historical_path(
    sequence: HistoricalSequence,
    *,
    duration_seconds: int = 900,
    block_seconds: int = 30,
    seed: int = 20260904,
    index: int = 0,
) -> ScenarioPath:
    """Circular block-bootstrap one real path into a new one-second research scenario."""
    if not 60 <= int(duration_seconds) <= 86_400:
        raise ValueError("duration_seconds must be between 60 and 86400")
    base = _interpolate_history(sequence)
    if len(base) < 3:
        raise ValueError("Historical sequence is too short to resample")
    duration_seconds = int(duration_seconds)
    block_seconds = max(1, min(int(block_seconds), len(base) - 1))
    scenario_seed = int(seed) + index * 7_919
    rng = random.Random(scenario_seed)
    volatility_scale = rng.uniform(.82, 1.18)

    price_returns = [math.log(base[i].price_usd / base[i - 1].price_usd) for i in range(1, len(base))]
    liquidity_returns = [
        math.log(max(base[i].liquidity_usd, 1e-9) / max(base[i - 1].liquidity_usd, 1e-9))
        for i in range(1, len(base))
    ]
    market_cap_returns = [
        math.log(max(base[i].market_cap_usd, 1e-9) / max(base[i - 1].market_cap_usd, 1e-9))
        for i in range(1, len(base))
    ]
    sampled_indices: list[int] = []
    while len(sampled_indices) < duration_seconds:
        start_index = rng.randrange(len(price_returns))
        sampled_indices.extend(
            (start_index + offset) % len(price_returns) for offset in range(block_seconds)
        )
    sampled_indices = sampled_indices[:duration_seconds]

    price = base[0].price_usd
    liquidity = base[0].liquidity_usd
    market_cap = base[0].market_cap_usd
    start_time = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(
        seconds=(abs(scenario_seed) % (90 * 24 * 3600))
    )
    cumulative_volume = 0.0
    rug_happened = False
    ticks = [SequenceTick(
        second_offset=0, captured_at=start_time.isoformat(), price_usd=price,
        liquidity_usd=liquidity, market_cap_usd=market_cap, volume_1s_usd=0.0,
        buys_1s=0, sells_1s=0, cumulative_volume_usd=0.0,
        social_data_available=base[0].social_data_available,
    )]
    for second, source_index in enumerate(sampled_indices, start=1):
        price *= math.exp(price_returns[source_index] * volatility_scale)
        liquidity *= math.exp(liquidity_returns[source_index])
        market_cap *= math.exp(market_cap_returns[source_index] * volatility_scale)
        # Bound only numerical explosions; the historical direction and clusters remain intact.
        price = _clamp(price, base[0].price_usd * .0001, base[0].price_usd * 10_000)
        liquidity = _clamp(liquidity, 0.0, max(base[0].liquidity_usd, 1.0) * 1_000)
        market_cap = _clamp(market_cap, 0.0, max(base[0].market_cap_usd, 1.0) * 10_000)
        source = base[source_index + 1]
        expected_tx = source.transaction_rate_per_second
        tx_count = max(0, int(round(rng.gauss(expected_tx, math.sqrt(max(expected_tx, .05))))))
        buys = sum(1 for _ in range(tx_count) if rng.random() < source.buy_ratio)
        sells = tx_count - buys
        volume = max(0.0, rng.lognormvariate(
            math.log(max(source.volume_rate_per_second, .01)), .18
        ))
        mentions = max(0, int(round(rng.gauss(
            source.social_mentions_rate_per_second,
            math.sqrt(max(source.social_mentions_rate_per_second, .05)),
        )))) if source.social_data_available else 0
        social_sentiment = source.social_sentiment if source.social_data_available else 0.0
        smart_wallet_flow = (
            rng.gauss(
                source.smart_wallet_flow_rate_per_second,
                abs(source.smart_wallet_flow_rate_per_second) * .18 + 1e-9,
            ) if source.social_data_available else 0.0
        )
        cumulative_volume += volume
        rug_event = not rug_happened and (
            liquidity <= max(base[0].liquidity_usd, 1e-9) * .20
            or price <= base[0].price_usd * .60
        )
        rug_happened = rug_happened or rug_event
        ticks.append(SequenceTick(
            second_offset=second,
            captured_at=(start_time + timedelta(seconds=second)).isoformat(),
            price_usd=price,
            liquidity_usd=liquidity,
            market_cap_usd=market_cap,
            volume_1s_usd=volume,
            buys_1s=buys,
            sells_1s=sells,
            cumulative_volume_usd=cumulative_volume,
            social_mentions_1s=mentions,
            social_sentiment=social_sentiment,
            smart_wallet_flow_1s_usd=smart_wallet_flow,
            social_data_available=source.social_data_available,
            rug_event=rug_event,
        ))
    family = _classify_path(ticks)
    scenario_id = _scenario_id(
        "HISTORICAL_RESAMPLE", family, scenario_seed, index, sequence.address_hash
    )
    return ScenarioPath(
        scenario_id=scenario_id,
        origin="HISTORICAL_RESAMPLE",
        family=family,
        seed=scenario_seed,
        ticks=tuple(ticks),
        source_fingerprint=(
            sequence.source_integrity["source_fingerprint"]
            if sequence.source_integrity is not None else sequence.source_database_sha256
        ),
        metadata={
            "generator_version": GENERATOR_VERSION,
            "method": "circular_block_bootstrap_on_interpolated_one_second_log_returns",
            "block_seconds": block_seconds,
            "volatility_scale": volatility_scale,
            "source_address_sha256": sequence.address_hash,
            "source_symbol": sequence.symbol,
            "source_observations": len(sequence.observations),
            "source_database_sha256": sequence.source_database_sha256,
            "source_integrity": sequence.source_integrity,
            "one_second_cadence": True,
            "social_data_available": any(tick.social_data_available for tick in ticks),
            "data_domain": SIMULATION_DATA_DOMAIN,
            "eligible_for_neural_research": True,
            "eligible_for_real_training": False,
            "eligible_for_real_champion": False,
        },
    )


def _pct_change(current: float, previous: float) -> float:
    return (current / previous - 1.0) * 100.0 if previous > 0 else 0.0


def build_training_examples(
    scenario: ScenarioPath,
    *,
    stride_seconds: int = 30,
    horizons_seconds: Iterable[int] = DEFAULT_HORIZONS_SECONDS,
) -> list[dict[str, Any]]:
    """Build labelled SIMULATION_ONLY examples without touching real learning tables."""
    stride_seconds = max(1, int(stride_seconds))
    horizons = sorted({int(h) for h in horizons_seconds if int(h) > 0})
    ticks = scenario.ticks
    examples: list[dict[str, Any]] = []
    for observed_second in range(stride_seconds, scenario.duration_seconds, stride_seconds):
        observed = ticks[observed_second]
        prior_5 = ticks[max(0, observed_second - 5)]
        prior_15 = ticks[max(0, observed_second - 15)]
        prior_60 = ticks[max(0, observed_second - 60)]
        window_60 = ticks[max(0, observed_second - 59):observed_second + 1]
        total_buys = sum(tick.buys_1s for tick in window_60)
        total_sells = sum(tick.sells_1s for tick in window_60)
        total_transactions = total_buys + total_sells
        social_mentions = sum(tick.social_mentions_1s for tick in window_60)
        mentioned_sentiment = sum(
            tick.social_sentiment * max(1, tick.social_mentions_1s)
            for tick in window_60 if tick.social_data_available
        )
        sentiment_weight = sum(
            max(1, tick.social_mentions_1s)
            for tick in window_60 if tick.social_data_available
        )
        social_sentiment = mentioned_sentiment / sentiment_weight if sentiment_weight else 0.0
        smart_wallet_flow = sum(tick.smart_wallet_flow_1s_usd for tick in window_60)
        volume_60s = sum(tick.volume_1s_usd for tick in window_60)
        market_flow = (total_buys - total_sells) / max(1, total_transactions)
        smart_flow_ratio = smart_wallet_flow / max(1.0, volume_60s)
        hype_score = max(0.0, social_sentiment) * math.log1p(social_mentions)
        social_flow_divergence = hype_score - market_flow - smart_flow_ratio
        features = {
            "observed_second": observed_second,
            "price_usd": observed.price_usd,
            "liquidity_usd": observed.liquidity_usd,
            "market_cap_usd": observed.market_cap_usd,
            "liquidity_to_market_cap": observed.liquidity_usd / max(observed.market_cap_usd, 1e-12),
            "return_5s_pct": _pct_change(observed.price_usd, prior_5.price_usd),
            "return_15s_pct": _pct_change(observed.price_usd, prior_15.price_usd),
            "return_60s_pct": _pct_change(observed.price_usd, prior_60.price_usd),
            "liquidity_change_60s_pct": _pct_change(observed.liquidity_usd, prior_60.liquidity_usd),
            "volume_60s_usd": volume_60s,
            "transactions_60s": total_transactions,
            "buy_ratio_60s": total_buys / max(1, total_transactions),
            "social_mentions_60s": social_mentions,
            "social_sentiment_60s": social_sentiment,
            "smart_wallet_net_flow_60s_usd": smart_wallet_flow,
            "social_flow_divergence": social_flow_divergence,
            "social_data_available": any(tick.social_data_available for tick in window_60),
            "sequence_native": True,
            "rug_seen": any(tick.rug_event for tick in ticks[:observed_second + 1]),
        }
        for horizon in horizons:
            if observed_second + horizon > scenario.duration_seconds:
                continue
            future = ticks[observed_second + 1:observed_second + horizon + 1]
            returns = [_pct_change(tick.price_usd, observed.price_usd) for tick in future]
            liquidity_floor = min(tick.liquidity_usd for tick in future) / max(observed.liquidity_usd, 1e-12)
            rug = any(tick.rug_event for tick in future) or min(returns) <= -40 or liquidity_floor <= .20
            profit_potential = max(0.0, max(returns))
            price_risk = max(0.0, -min(returns))
            liquidity_risk = max(0.0, (1.0 - liquidity_floor) * 100.0)
            risk_exposure = max(price_risk, liquidity_risk, 100.0 if rug else 0.0)
            labels = {
                "hit_5": max(returns) >= 5,
                "hit_10": max(returns) >= 10,
                "hit_25": max(returns) >= 25,
                "rug": rug,
                "stagnant": not rug and max(returns) < 3 and min(returns) > -10,
                "max_return_pct": max(returns),
                "min_return_pct": min(returns),
                "return_pct": returns[-1],
                "profit_potential_pct": profit_potential,
                "risk_exposure_pct": risk_exposure,
                "balanced_utility_pct": .5 * profit_potential - .5 * risk_exposure,
                "liquidity_drawdown_pct": liquidity_risk,
            }
            example_key = f"{scenario.scenario_id}|{observed_second}|{horizon}"
            examples.append({
                "example_id": "ex-" + hashlib.sha256(example_key.encode("utf-8")).hexdigest()[:28],
                "scenario_id": scenario.scenario_id,
                "observed_second": observed_second,
                "horizon_seconds": horizon,
                "data_domain": SIMULATION_DATA_DOMAIN,
                "eligible_for_neural_research": True,
                "eligible_for_real_training": False,
                "eligible_for_real_champion": False,
                "features": features,
                "labels": labels,
                "provenance": {
                    "origin": scenario.origin,
                    "family": scenario.family,
                    "scenario_seed": scenario.seed,
                    "sequence_checksum": scenario.checksum,
                    "source_address_sha256": scenario.metadata.get("source_address_sha256"),
                    "generator_version": GENERATOR_VERSION,
                    "feature_cutoff_second": observed_second,
                    "label_start_second": observed_second + 1,
                    "label_end_second": observed_second + horizon,
                    "profit_risk_weights": {"profit": .5, "risk": .5},
                },
            })
    return examples


class SimulationStore:
    """Dedicated storage that cannot be mistaken for the live/paper Store."""

    data_domain = SIMULATION_DATA_DOMAIN

    def __init__(
        self,
        path: Path,
        *,
        real_source_path: Path | None = None,
        read_only: bool = False,
    ):
        self.path = Path(path).resolve()
        if real_source_path is not None and self.path == Path(real_source_path).resolve():
            raise ValueError("Simulation output and real historical source must be different databases")
        if read_only and not self.path.is_file():
            raise FileNotFoundError(f"Simulation database not found: {self.path}")
        if self.path.exists() and self.path.stat().st_size:
            probe = _readonly_connection(self.path)
            try:
                tables = {
                    str(row[0]) for row in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                probe.close()
            real_tables = {"positions", "trades", "learning_predictions", "account_state"}
            if tables & real_tables or (tables and "simulation_metadata" not in tables):
                raise ValueError("Refusing to open a real or unknown database as simulation storage")
            if "simulation_metadata" in tables:
                version_probe = _readonly_connection(self.path)
                try:
                    row = version_probe.execute(
                        "SELECT schema_version FROM simulation_metadata WHERE id=1"
                    ).fetchone()
                finally:
                    version_probe.close()
                existing_version = int(row[0]) if row is not None else 0
                if existing_version != SIMULATION_SCHEMA_VERSION:
                    raise ValueError(
                        f"Simulation schema v{existing_version} is incompatible with v{SIMULATION_SCHEMA_VERSION}; "
                        "create a new v0.6 simulation database"
                    )
        if read_only:
            self.conn = _readonly_connection(self.path)
            metadata = self.conn.execute(
                "SELECT * FROM simulation_metadata WHERE id=1"
            ).fetchone()
            if (
                metadata is None
                or metadata["data_domain"] != SIMULATION_DATA_DOMAIN
                or int(metadata["schema_version"]) != SIMULATION_SCHEMA_VERSION
                or not metadata["eligible_for_neural_research"]
                or metadata["eligible_for_real_training"]
                or metadata["eligible_for_real_champion"]
                or float(metadata["execution_authority_pct"]) != 0.0
            ):
                self.conn.close()
                raise ValueError("Simulation storage domain marker is missing or invalid")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(SIMULATION_SCHEMA)
        self.conn.execute(
            """INSERT OR IGNORE INTO simulation_metadata(
            id,schema_version,data_domain,eligible_for_neural_research,eligible_for_real_training,
            eligible_for_real_champion,execution_authority_pct,created_at,generator_version
            ) VALUES(1,?,'SIMULATION',1,0,0,0,?,?)""",
            (SIMULATION_SCHEMA_VERSION, _utc_now(), GENERATOR_VERSION),
        )
        metadata = self.conn.execute("SELECT * FROM simulation_metadata WHERE id=1").fetchone()
        if (
            metadata is None
            or metadata["data_domain"] != SIMULATION_DATA_DOMAIN
            or int(metadata["schema_version"]) != SIMULATION_SCHEMA_VERSION
            or not metadata["eligible_for_neural_research"]
        ):
            self.conn.close()
            raise ValueError("Simulation storage domain marker is missing or invalid")
        if (
            metadata["eligible_for_real_training"]
            or metadata["eligible_for_real_champion"]
            or float(metadata["execution_authority_pct"]) != 0.0
        ):
            self.conn.close()
            raise ValueError("Simulation storage must never be eligible for real model training")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def persist_run(
        self,
        scenarios: Sequence[ScenarioPath],
        *,
        seed: int,
        config: dict[str, Any],
        example_stride_seconds: int = 30,
    ) -> dict[str, Any]:
        if not scenarios:
            raise ValueError("At least one simulation scenario is required")
        if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
            raise ValueError("Scenario IDs must be unique within a run")
        examples_by_scenario = {
            scenario.scenario_id: build_training_examples(
                scenario, stride_seconds=example_stride_seconds
            ) for scenario in scenarios
        }
        tick_count = sum(len(scenario.ticks) for scenario in scenarios)
        example_count = sum(len(rows) for rows in examples_by_scenario.values())
        run_payload = {
            "seed": int(seed),
            "config": config,
            "scenario_checksums": [scenario.checksum for scenario in scenarios],
        }
        run_checksum = hashlib.sha256(json.dumps(
            run_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        run_id = "run-" + uuid.uuid4().hex
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """INSERT INTO simulation_runs(
                run_id,created_at,seed,scenario_count,tick_count,example_count,config_json,run_checksum
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (run_id, _utc_now(), int(seed), len(scenarios), tick_count, example_count,
                 json.dumps(config, sort_keys=True), run_checksum),
            )
            for scenario in scenarios:
                self.conn.execute(
                    """INSERT INTO simulation_scenarios(
                    scenario_id,run_id,origin,family,seed,duration_seconds,
                    source_fingerprint,sequence_checksum,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (scenario.scenario_id, run_id, scenario.origin, scenario.family,
                     scenario.seed, scenario.duration_seconds, scenario.source_fingerprint,
                     scenario.checksum, json.dumps(scenario.metadata, sort_keys=True)),
                )
                self.conn.executemany(
                    """INSERT INTO simulation_ticks(
                    scenario_id,second_offset,captured_at,price_usd,liquidity_usd,
                    market_cap_usd,volume_1s_usd,buys_1s,sells_1s,
                    cumulative_volume_usd,social_mentions_1s,social_sentiment,
                    smart_wallet_flow_1s_usd,social_data_available,rug_event,data_domain
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'SIMULATION')""",
                    [(
                        scenario.scenario_id, tick.second_offset, tick.captured_at,
                        tick.price_usd, tick.liquidity_usd, tick.market_cap_usd,
                        tick.volume_1s_usd, tick.buys_1s, tick.sells_1s,
                        tick.cumulative_volume_usd, tick.social_mentions_1s,
                        tick.social_sentiment, tick.smart_wallet_flow_1s_usd,
                        int(tick.social_data_available), int(tick.rug_event),
                    ) for tick in scenario.ticks],
                )
                self.conn.executemany(
                    """INSERT INTO simulation_training_examples(
                    example_id,scenario_id,observed_second,horizon_seconds,data_domain,
                    eligible_for_neural_research,eligible_for_real_training,eligible_for_real_champion,
                    features_json,labels_json,provenance_json
                    ) VALUES(?,?,?,?,'SIMULATION',1,0,0,?,?,?)""",
                    [(
                        row["example_id"], row["scenario_id"], row["observed_second"],
                        row["horizon_seconds"], json.dumps(row["features"], sort_keys=True),
                        json.dumps(row["labels"], sort_keys=True),
                        json.dumps(row["provenance"], sort_keys=True),
                    ) for row in examples_by_scenario[scenario.scenario_id]],
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {
            "run_id": run_id,
            "run_checksum": run_checksum,
            "scenario_count": len(scenarios),
            "tick_count": tick_count,
            "training_example_count": example_count,
            "data_domain": SIMULATION_DATA_DOMAIN,
            "eligible_for_neural_research": True,
            "eligible_for_real_training": False,
            "eligible_for_real_champion": False,
            "execution_authority_pct": 0,
        }

    def summary(self) -> dict[str, Any]:
        metadata = dict(self.conn.execute("SELECT * FROM simulation_metadata WHERE id=1").fetchone())
        totals = dict(self.conn.execute(
            """SELECT
            (SELECT COUNT(*) FROM simulation_runs) AS runs,
            (SELECT COUNT(*) FROM simulation_scenarios) AS scenarios,
            (SELECT COUNT(*) FROM simulation_ticks) AS ticks,
            (SELECT COUNT(*) FROM simulation_training_examples) AS training_examples"""
        ).fetchone())
        families = [dict(row) for row in self.conn.execute(
            """SELECT origin,family,COUNT(*) AS scenarios,
            SUM(duration_seconds+1) AS ticks FROM simulation_scenarios
            GROUP BY origin,family ORDER BY origin,family"""
        )]
        return {
            "database": str(self.path),
            "metadata": metadata,
            "totals": totals,
            "families": families,
            "separation_guards": {
                "separate_database_required": True,
                "real_source_opened_read_only": True,
                "real_learning_tables_present": False,
                "simulation_rows_eligible_for_neural_research": True,
                "simulation_rows_eligible_for_real_training": False,
                "simulation_rows_eligible_for_real_champion": False,
                "execution_authority_pct": 0,
            },
        }

    def report(self) -> dict[str, Any]:
        """Return scenario performance and risk diagnostics without changing data."""
        rows = self.conn.execute(
            """SELECT s.scenario_id,s.origin,s.family,
            MIN(CASE WHEN t.second_offset=0 THEN t.price_usd END) AS start_price,
            MIN(CASE WHEN t.second_offset=0 THEN t.liquidity_usd END) AS start_liquidity,
            MIN(t.price_usd) AS min_price,MAX(t.price_usd) AS max_price,
            MIN(t.liquidity_usd) AS min_liquidity,
            MAX(CASE WHEN t.rug_event=1 THEN 1 ELSE 0 END) AS rug_event,
            SUM(t.social_mentions_1s) AS social_mentions,
            AVG(CASE WHEN t.social_data_available=1 THEN t.social_sentiment END) AS social_sentiment,
            SUM(t.smart_wallet_flow_1s_usd) AS smart_wallet_flow,
            MAX(CASE WHEN t.second_offset=s.duration_seconds THEN t.price_usd END) AS final_price
            FROM simulation_scenarios s JOIN simulation_ticks t USING(scenario_id)
            GROUP BY s.scenario_id,s.origin,s.family ORDER BY s.origin,s.family,s.scenario_id"""
        ).fetchall()
        groups: dict[tuple[str, str], list[dict[str, float]]] = {}
        for row in rows:
            start_price = max(float(row["start_price"]), 1e-12)
            start_liquidity = max(float(row["start_liquidity"]), 1e-12)
            upside = max(0.0, (float(row["max_price"]) / start_price - 1.0) * 100.0)
            downside = max(0.0, (1.0 - float(row["min_price"]) / start_price) * 100.0)
            liquidity_drawdown = max(
                0.0, (1.0 - float(row["min_liquidity"]) / start_liquidity) * 100.0
            )
            risk = max(downside, liquidity_drawdown, 100.0 if row["rug_event"] else 0.0)
            groups.setdefault((str(row["origin"]), str(row["family"])), []).append({
                "upside": upside,
                "downside": downside,
                "liquidity_drawdown": liquidity_drawdown,
                "balanced_utility": .5 * upside - .5 * risk,
                "final_return": (float(row["final_price"]) / start_price - 1.0) * 100.0,
                "rug": float(bool(row["rug_event"])),
                "social_mentions": float(row["social_mentions"] or 0.0),
                "social_sentiment": float(row["social_sentiment"] or 0.0),
                "smart_wallet_flow": float(row["smart_wallet_flow"] or 0.0),
            })

        family_metrics = []
        for (origin, family), values in sorted(groups.items()):
            def average(key: str) -> float:
                return round(sum(value[key] for value in values) / len(values), 6)

            family_metrics.append({
                "origin": origin,
                "family": family,
                "scenarios": len(values),
                "avg_upside_pct": average("upside"),
                "avg_downside_pct": average("downside"),
                "avg_liquidity_drawdown_pct": average("liquidity_drawdown"),
                "avg_final_return_pct": average("final_return"),
                "avg_balanced_utility_pct": average("balanced_utility"),
                "rug_rate": average("rug"),
                "avg_social_mentions": average("social_mentions"),
                "avg_social_sentiment": average("social_sentiment"),
                "avg_smart_wallet_flow_usd": average("smart_wallet_flow"),
            })
        present = {
            str(row["family"]) for row in rows if str(row["origin"]) == "SYNTHETIC"
        }
        return {
            **self.summary(),
            "required_synthetic_families": list(SYNTHETIC_FAMILIES),
            "synthetic_family_coverage": {
                "present": sorted(present),
                "missing": sorted(set(SYNTHETIC_FAMILIES) - present),
                "complete": set(SYNTHETIC_FAMILIES).issubset(present),
            },
            "profit_risk_weights": {"profit": .5, "risk": .5},
            "family_metrics": family_metrics,
        }

    def export_training_jsonl(self, output_path: Path) -> dict[str, Any]:
        output_path = Path(output_path).resolve()
        if output_path == self.path:
            raise ValueError("Training export cannot overwrite the simulation database")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.conn.execute(
            """SELECT e.*,s.origin,s.family,s.sequence_checksum,s.run_id
            FROM simulation_training_examples e
            JOIN simulation_scenarios s ON s.scenario_id=e.scenario_id
            ORDER BY s.run_id,e.scenario_id,e.observed_second,e.horizon_seconds"""
        ).fetchall()
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        digest = hashlib.sha256()
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                payload = {
                    "schema_version": SIMULATION_SCHEMA_VERSION,
                    "data_domain": SIMULATION_DATA_DOMAIN,
                    "eligible_for_neural_research": True,
                    "eligible_for_real_training": False,
                    "eligible_for_real_champion": False,
                    "execution_authority_pct": 0,
                    "run_id": row["run_id"],
                    "scenario_id": row["scenario_id"],
                    "origin": row["origin"],
                    "family": row["family"],
                    "sequence_checksum": row["sequence_checksum"],
                    "observed_second": row["observed_second"],
                    "horizon_seconds": row["horizon_seconds"],
                    "features": json.loads(row["features_json"]),
                    "labels": json.loads(row["labels_json"]),
                    "provenance": json.loads(row["provenance_json"]),
                }
                line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
                handle.write(line)
                digest.update(line.encode("utf-8"))
        temporary.replace(output_path)
        return {
            "output": str(output_path),
            "rows": len(rows),
            "sha256": digest.hexdigest(),
            "data_domain": SIMULATION_DATA_DOMAIN,
            "eligible_for_neural_research": True,
            "eligible_for_real_training": False,
            "eligible_for_real_champion": False,
        }


def run_simulation(
    output_database: Path,
    *,
    seed: int = 20260904,
    duration_seconds: int = 900,
    synthetic_per_family: int = 1,
    families: Sequence[str] = SYNTHETIC_FAMILIES,
    historical_database: Path | None = None,
    historical_paths: int = 0,
    block_seconds: int = 30,
    example_stride_seconds: int = 30,
) -> dict[str, Any]:
    """Generate and persist a bounded mixed scenario run."""
    families = tuple(dict.fromkeys(
        SYNTHETIC_FAMILY_ALIASES.get(family.lower().strip(), family.lower().strip())
        for family in families
    ))
    unknown = sorted(set(families) - set(SYNTHETIC_FAMILIES))
    if unknown:
        raise ValueError(f"Unknown synthetic families: {', '.join(unknown)}")
    if not 0 <= int(synthetic_per_family) <= 10_000:
        raise ValueError("synthetic_per_family must be between 0 and 10000")
    if not 0 <= int(historical_paths) <= 10_000:
        raise ValueError("historical_paths must be between 0 and 10000")
    if historical_paths and historical_database is None:
        raise ValueError("historical_database is required when historical_paths is non-zero")
    requested_scenarios = len(families) * int(synthetic_per_family) + int(historical_paths)
    requested_ticks = requested_scenarios * (int(duration_seconds) + 1)
    if requested_ticks > 5_000_000:
        raise ValueError(
            "A single in-memory run is limited to 5,000,000 sequence ticks; split it into append-only runs"
        )

    scenarios: list[ScenarioPath] = []
    for family in families:
        for index in range(int(synthetic_per_family)):
            scenarios.append(generate_synthetic_path(
                family, duration_seconds=duration_seconds, seed=seed, index=index
            ))
    historical_integrity = None
    if historical_paths:
        sequences = load_historical_sequences(Path(historical_database))
        if not sequences:
            raise ValueError("No historical token has enough valid observations to resample")
        historical_integrity = sequences[0].source_integrity
        rng = random.Random(int(seed) ^ 0x5A17)
        for index in range(int(historical_paths)):
            source = sequences[rng.randrange(len(sequences))]
            scenarios.append(resample_historical_path(
                source, duration_seconds=duration_seconds, block_seconds=block_seconds,
                seed=seed + 50_000_000, index=index,
            ))
    if not scenarios:
        raise ValueError("The requested run would contain no scenarios")

    store = SimulationStore(
        Path(output_database), real_source_path=historical_database
    )
    try:
        config = {
            "generator_version": GENERATOR_VERSION,
            "seed": int(seed),
            "duration_seconds": int(duration_seconds),
            "synthetic_per_family": int(synthetic_per_family),
            "families": list(families),
            "historical_paths": int(historical_paths),
            "block_seconds": int(block_seconds),
            "example_stride_seconds": int(example_stride_seconds),
            "historical_source_sha256": (
                historical_integrity["sha256"] if historical_integrity is not None else None
            ),
            "historical_source_integrity": historical_integrity,
            "data_domain": SIMULATION_DATA_DOMAIN,
            "eligible_for_neural_research": True,
            "eligible_for_real_training": False,
            "eligible_for_real_champion": False,
            "execution_authority_pct": 0,
        }
        run = store.persist_run(
            scenarios, seed=seed, config=config,
            example_stride_seconds=example_stride_seconds,
        )
        return {**run, "database": str(store.path), "status": store.summary()}
    finally:
        store.close()
