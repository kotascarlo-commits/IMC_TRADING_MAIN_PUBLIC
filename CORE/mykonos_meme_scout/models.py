from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class SecuritySignals:
    checked: bool = False
    mint_authority_disabled: bool | None = None
    freeze_authority_disabled: bool | None = None
    honeypot: bool | None = None
    top10_holder_pct: float | None = None
    creator_holder_pct: float | None = None
    metadata_mutable: bool | None = None
    non_transferable: bool | None = None
    transfer_hook_present: bool | None = None
    malicious_creator: bool | None = None
    # Local request observation, never inferred from a market snapshot's time.
    checked_at: str | None = None


@dataclass(slots=True)
class TokenSnapshot:
    address: str
    symbol: str
    name: str
    pair_address: str
    dex_id: str
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_5m_usd: float
    volume_1h_usd: float
    buys_5m: int
    sells_5m: int
    price_change_5m_pct: float
    pair_created_at_ms: int
    captured_at: str = field(default_factory=utc_now)
    chain_id: str = "solana"
    profile_present: bool = False
    boost_amount: float = 0.0
    jupiter_price_impact_pct: float | None = None
    discovery_source: str = "unknown"
    market_data_stale: bool = False
    security: SecuritySignals = field(default_factory=SecuritySignals)

    @property
    def age_minutes(self) -> float:
        created = datetime.fromtimestamp(self.pair_created_at_ms / 1000, timezone.utc)
        captured = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        return max(0.0, (captured - created).total_seconds() / 60)

    @property
    def liquidity_ratio(self) -> float:
        return self.liquidity_usd / self.market_cap_usd if self.market_cap_usd > 0 else 0.0

    @property
    def transactions_5m(self) -> int:
        return self.buys_5m + self.sells_5m

    @property
    def activity_window_minutes(self) -> float:
        # For a pool younger than five minutes, the m5 bucket is effectively a
        # launch-to-now bucket. Normalizing by age prevents young pools being
        # penalized for not having five minutes of history yet.
        return max(0.33, min(5.0, self.age_minutes))

    @property
    def volume_rate_per_minute(self) -> float:
        return self.volume_5m_usd / self.activity_window_minutes

    @property
    def transaction_rate_per_minute(self) -> float:
        return self.transactions_5m / self.activity_window_minutes

    @property
    def buy_ratio(self) -> float:
        return self.buys_5m / max(1, self.transactions_5m)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MicroMetrics:
    has_previous: bool = False
    elapsed_seconds: float | None = None
    price_velocity_pct_per_minute: float | None = None
    liquidity_velocity_pct_per_minute: float | None = None
    activity_acceleration: float | None = None
    buy_ratio_change: float | None = None


@dataclass(slots=True)
class Assessment:
    eligible: bool
    score: int
    confidence: int
    label: str
    hard_failures: list[str]
    warnings: list[str]
    components: dict[str, int]

    @property
    def decision(self) -> str:
        return "PASS" if self.eligible else "WATCH"
