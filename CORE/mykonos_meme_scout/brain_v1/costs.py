"""Explicit, conservative PAPER costs; no claim of executable venue quotes."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class CostPolicy:
    fee_per_side_pct: float = 1.0
    slippage_per_side_pct: float = .35
    latency_per_side_pct: float = .10
    notional_usd: float = 10.0
    maximum_impact_pct: float = 2.0

    def __post_init__(self):
        for value in asdict(self).values():
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Cost assumptions must be finite and positive")

    def estimate(self, liquidity_usd: float, *, stress: float = 1.0) -> float | None:
        if not math.isfinite(liquidity_usd) or liquidity_usd <= 0:
            return None
        if not math.isfinite(stress) or stress < 1:
            raise ValueError("Stress must be >= 1")
        # Constant-product first-order impact reserve; no unknown liquidity credit.
        impact = 200.0 * self.notional_usd / liquidity_usd
        if impact > self.maximum_impact_pct:
            return None
        return stress * (2 * (self.fee_per_side_pct + self.slippage_per_side_pct
                              + self.latency_per_side_pct) + 2 * impact)

    def as_dict(self):
        return {**asdict(self), "base_round_trip_pct": 2.9,
                "stress_multiplier": 2.0,
                "assumption": "Paper estimate: fees + slippage + latency + liquidity impact; unverified against live fills"}


def net_return(gross_return_pct: float, cost_pct: float) -> float:
    if not all(math.isfinite(x) for x in (gross_return_pct, cost_pct)) or cost_pct <= 0:
        raise ValueError("Returns/costs must be finite; costs positive")
    # Return-dependent exit cost: higher proceeds incur correspondingly higher fees.
    multiplier = max(0.0, 1.0 - cost_pct / 200.0)
    return max(-100.0, (max(0.0, 1 + gross_return_pct / 100.0) * multiplier**2 - 1) * 100.0)

