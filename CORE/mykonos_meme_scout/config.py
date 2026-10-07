from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class Config:
    database: str = "data/imc-main.db"
    initial_cash_usd: float = 1000.0
    scan_interval_seconds: int = 15
    # Scanner-side hard deadlines. These bound public API stalls without
    # weakening entry gates; missing security remains fail-closed.
    scan_discovery_request_timeout_seconds: float = 1.5
    scan_discovery_hard_deadline_seconds: float = 2.5
    scan_refresh_request_timeout_seconds: float = 1.5
    scan_refresh_hard_deadline_seconds: float = 2.5
    scan_security_request_timeout_seconds: float = 1.5
    scan_security_hard_deadline_seconds: float = 2.0
    position_watch_interval_seconds: float = 2.0
    position_watch_market_timeout_seconds: float = 1.25
    position_watch_fallback_timeout_seconds: float = 1.0
    position_watch_fallback_hedge_delay_seconds: float = 0.65
    position_watch_hard_deadline_seconds: float = 1.8
    position_watch_target_latency_seconds: float = 2.5
    position_watch_warning_latency_seconds: float = 3.0

    # First-15-minute launch window. Entries outside this window are impossible.
    minimum_entry_age_seconds: int = 20
    maximum_entry_age_minutes: int = 15
    preferred_entry_age_minutes: int = 5
    tracking_retention_minutes: int = 35

    # Paper-research gates. These are hypotheses, not claimed optimal parameters.
    minimum_score: int = 72
    minimum_liquidity_usd: float = 15_000.0
    minimum_market_cap_usd: float = 20_000.0
    minimum_liquidity_to_market_cap: float = 0.02
    minimum_volume_rate_usd_per_minute: float = 1_500.0
    minimum_transactions_per_minute: float = 6.0
    minimum_buy_ratio: float = 0.57
    minimum_price_change_5m_pct: float = -5.0
    maximum_price_change_5m_pct: float = 120.0
    maximum_jupiter_price_impact_pct: float = 1.5

    # A second observation is deliberately required; at a 15s cadence this is fast
    # enough for launch trading while preventing one-tick entries.
    require_second_confirmation: bool = True
    learning_prediction_interval_seconds: int = 60

    # Continual neural shadow learning. The adaptive model updates itself from
    # newly resolved labels; the champion remains a validation-gated reference.
    neural_auto_check_seconds: int = 60
    neural_max_rows_per_horizon: int = 5000
    neural_bootstrap_minimum_rows_per_horizon: int = 500
    neural_new_rows_per_horizon: int = 120
    neural_high_drift_new_rows_per_horizon: int = 60
    neural_hidden_units: int = 12
    neural_update_epochs: int = 3
    neural_update_learning_rate: float = 0.0045

    # PAPER-only early-probe lane. This exists because third-party security
    # enrichment often lags brand-new launches. Known critical risks still block.
    # Unknown security may be sampled only with exceptional early flow, at tiny size.
    paper_probe_enabled: bool = True
    # Probe classification/learning remains active, but execution is shadow-only.
    paper_probe_execution_enabled: bool = False
    probe_max_age_minutes: int = 5
    probe_minimum_score: int = 50
    probe_minimum_liquidity_usd: float = 15_000.0
    probe_minimum_market_cap_usd: float = 5_000.0
    probe_minimum_volume_rate_usd_per_minute: float = 5_000.0
    probe_minimum_transactions_per_minute: float = 25.0
    probe_minimum_buy_ratio: float = 0.62
    # All EARLY_PROBE positions remain tiny. A security API clearing known
    # control flags is useful as a veto/completeness signal, but it is not
    # sufficient evidence to size up a first-minutes meme launch.
    probe_maximum_notional_pct: float = 0.25
    probe_unverified_maximum_notional_pct: float = 0.25

    # Rapid PAPER position watcher / rug shield. These do not loosen entries.
    rug_liquidity_velocity_exit_pct_per_minute: float = -35.0
    rug_price_velocity_exit_pct_per_minute: float = -35.0
    rug_flow_buy_ratio_exit: float = 0.40

    # Paper portfolio / execution assumptions.
    risk_per_trade_pct: float = 0.25
    maximum_early_exposure_pct: float = 1.5
    maximum_open_positions: int = 3
    estimated_cost_per_side_pct: float = 1.0
    hard_stop_pct: float = 12.0
    trailing_activation_pct: float = 10.0
    trailing_stop_pct: float = 8.0
    first_take_profit_pct: float = 15.0
    second_take_profit_pct: float = 30.0
    maximum_hold_minutes: int = 12
    no_progress_exit_minutes: int = 4
    no_progress_min_return_pct: float = 2.0
    daily_loss_kill_pct: float = 1.0
    weekly_loss_kill_pct: float = 3.0
    account_drawdown_kill_pct: float = 5.0
    live_trading_enabled: bool = False

    def __post_init__(self) -> None:
        if self.live_trading_enabled:
            raise ValueError("Live trading is deliberately unavailable in v0.4.1.")
        positive = {
            "initial_cash_usd": self.initial_cash_usd,
            "scan_interval_seconds": self.scan_interval_seconds,
            "scan_discovery_request_timeout_seconds": self.scan_discovery_request_timeout_seconds,
            "scan_discovery_hard_deadline_seconds": self.scan_discovery_hard_deadline_seconds,
            "scan_refresh_request_timeout_seconds": self.scan_refresh_request_timeout_seconds,
            "scan_refresh_hard_deadline_seconds": self.scan_refresh_hard_deadline_seconds,
            "scan_security_request_timeout_seconds": self.scan_security_request_timeout_seconds,
            "scan_security_hard_deadline_seconds": self.scan_security_hard_deadline_seconds,
            "position_watch_interval_seconds": self.position_watch_interval_seconds,
            "position_watch_market_timeout_seconds": self.position_watch_market_timeout_seconds,
            "position_watch_fallback_timeout_seconds": self.position_watch_fallback_timeout_seconds,
            "position_watch_fallback_hedge_delay_seconds": self.position_watch_fallback_hedge_delay_seconds,
            "position_watch_hard_deadline_seconds": self.position_watch_hard_deadline_seconds,
            "position_watch_target_latency_seconds": self.position_watch_target_latency_seconds,
            "position_watch_warning_latency_seconds": self.position_watch_warning_latency_seconds,
            "minimum_score": self.minimum_score,
            "maximum_entry_age_minutes": self.maximum_entry_age_minutes,
            "hard_stop_pct": self.hard_stop_pct,
            "maximum_open_positions": self.maximum_open_positions,
            "neural_auto_check_seconds": self.neural_auto_check_seconds,
            "neural_max_rows_per_horizon": self.neural_max_rows_per_horizon,
            "neural_bootstrap_minimum_rows_per_horizon": self.neural_bootstrap_minimum_rows_per_horizon,
            "neural_new_rows_per_horizon": self.neural_new_rows_per_horizon,
            "neural_high_drift_new_rows_per_horizon": self.neural_high_drift_new_rows_per_horizon,
            "neural_hidden_units": self.neural_hidden_units,
            "neural_update_epochs": self.neural_update_epochs,
            "neural_update_learning_rate": self.neural_update_learning_rate,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"Config values must be positive: {', '.join(invalid)}")
        for request_name, request_value, deadline_name, deadline_value in [
            ("scan_discovery_request_timeout_seconds", self.scan_discovery_request_timeout_seconds,
             "scan_discovery_hard_deadline_seconds", self.scan_discovery_hard_deadline_seconds),
            ("scan_refresh_request_timeout_seconds", self.scan_refresh_request_timeout_seconds,
             "scan_refresh_hard_deadline_seconds", self.scan_refresh_hard_deadline_seconds),
            ("scan_security_request_timeout_seconds", self.scan_security_request_timeout_seconds,
             "scan_security_hard_deadline_seconds", self.scan_security_hard_deadline_seconds),
        ]:
            if request_value >= deadline_value:
                raise ValueError(f"{request_name} must be below {deadline_name}")
        if not 1 <= self.minimum_score <= 100:
            raise ValueError("minimum_score must be between 1 and 100")
        if self.preferred_entry_age_minutes > self.maximum_entry_age_minutes:
            raise ValueError("preferred_entry_age_minutes cannot exceed maximum_entry_age_minutes")
        if self.minimum_entry_age_seconds >= self.maximum_entry_age_minutes * 60:
            raise ValueError("minimum_entry_age_seconds must be inside the entry window")
        if not 0.5 <= self.minimum_buy_ratio <= 1.0:
            raise ValueError("minimum_buy_ratio must be between 0.5 and 1.0")
        if not 0.5 <= self.probe_minimum_buy_ratio <= 1.0:
            raise ValueError("probe_minimum_buy_ratio must be between 0.5 and 1.0")
        if self.probe_max_age_minutes > self.maximum_entry_age_minutes:
            raise ValueError("probe_max_age_minutes cannot exceed maximum_entry_age_minutes")
        if not 0 < self.probe_unverified_maximum_notional_pct <= self.probe_maximum_notional_pct:
            raise ValueError("probe_unverified_maximum_notional_pct must be >0 and <= probe_maximum_notional_pct")
        if self.position_watch_warning_latency_seconds < self.position_watch_target_latency_seconds:
            raise ValueError("position_watch_warning_latency_seconds cannot be below target latency")
        if self.position_watch_hard_deadline_seconds >= self.position_watch_target_latency_seconds:
            raise ValueError("position_watch_hard_deadline_seconds must remain below target latency")
        if self.position_watch_fallback_hedge_delay_seconds >= self.position_watch_hard_deadline_seconds:
            raise ValueError("position_watch_fallback_hedge_delay_seconds must be below hard deadline")
        if self.neural_high_drift_new_rows_per_horizon > self.neural_new_rows_per_horizon:
            raise ValueError("neural_high_drift_new_rows_per_horizon cannot exceed normal neural trigger")
        if self.neural_bootstrap_minimum_rows_per_horizon < 50:
            raise ValueError("neural_bootstrap_minimum_rows_per_horizon must be at least 50")

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        raw: dict = {}
        chosen = path or os.getenv("MYKONOS_CONFIG")
        if chosen:
            raw = json.loads(Path(chosen).read_text(encoding="utf-8"))
        valid = set(asdict(cls()))
        unknown = set(raw) - valid
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        return cls(**raw)

    def resolve_database(self, base: Path) -> Path:
        path = Path(self.database)
        return path if path.is_absolute() else base / path
