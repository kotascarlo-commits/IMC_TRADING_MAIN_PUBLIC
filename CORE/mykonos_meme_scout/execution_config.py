"""Shared fail-closed configuration for the existing execution and UI paths."""
import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config/imc-execution.json'


class ConfigurationError(ValueError):
    pass


def validate_execution_config(cfg):
    if (not isinstance(cfg, dict) or type(cfg.get('schema_version')) is not int or cfg['schema_version'] not in (1, 2)
            or cfg.get('host') != '127.0.0.1'):
        raise ConfigurationError('invalid_config')
    validation_live = (cfg['schema_version'] == 2 and cfg.get('execution_provider') == 'JUPITER_V2'
                       and cfg.get('execution_mode') == 'THREE_TRADE_VALIDATION')
    if cfg['schema_version'] == 2 and not validation_live:
        raise ConfigurationError('invalid_config')
    enabled = validation_live and cfg.get('execution_authority_pct') == 100
    if type(cfg.get('execution_authority_pct')) is not int or cfg['execution_authority_pct'] != (100 if enabled else 0):
        raise ConfigurationError('live_authority_disabled_required')
    for key in ('broadcast_enabled', 'live_trading_enabled', 'signing', 'wallet_access'):
        if type(cfg.get(key)) is not bool or cfg[key] != enabled:
            raise ConfigurationError('live_authority_disabled_required')
    if validation_live and (cfg.get('max_open_positions') != 1 or cfg.get('max_session_trades') != 3):
        raise ConfigurationError('validation_limits_required')
    integers = ('port', 'max_open_positions', 'max_session_trades', 'max_slippage_bps',
                'max_price_impact_bps', 'max_price_deviation_bps', 'max_api_failures',
                'max_http_body_bytes', 'max_snapshot_pages', 'dashboard_max_response_bytes',
                'dashboard_control_body_bytes', 'dashboard_start_poll_attempts')
    for key in integers:
        if type(cfg.get(key)) is not int or cfg[key] <= 0:
            raise ConfigurationError('invalid_integer_config:' + key)
    if cfg['port'] > 65535 or any(cfg[k] > 10000 for k in
        ('max_slippage_bps', 'max_price_impact_bps', 'max_price_deviation_bps')):
        raise ConfigurationError('invalid_config_range')
    if any(cfg[k] > 16 * 1024 * 1024 for k in ('max_http_body_bytes','dashboard_max_response_bytes','dashboard_control_body_bytes')) or cfg['max_snapshot_pages'] > 1000 or cfg['dashboard_start_poll_attempts'] > 100:
        raise ConfigurationError('unbounded_resource_config')
    for key in ('signal_max_age_seconds', 'quote_max_age_seconds', 'request_timeout_seconds',
                'reconciliation_max_age_seconds', 'http_timeout_seconds', 'rpc_timeout_seconds',
                'bridge_poll_seconds', 'bridge_error_seconds', 'bridge_rate_limit_seconds',
                'reconciliation_poll_seconds', 'dashboard_gateway_timeout_seconds',
                'dashboard_control_timeout_seconds', 'process_query_timeout_seconds',
                'dashboard_start_poll_seconds', 'ledger_lock_timeout_seconds'):
        value = cfg.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ConfigurationError('invalid_time_config:' + key)
    if cfg['ledger_lock_timeout_seconds'] > 30:
        raise ConfigurationError('unbounded_ledger_lock_timeout')
    if cfg['quote_max_age_seconds'] >= 30:
        raise ConfigurationError('unbounded_execution_quote_age')
    for key in ('reserve_lamports', 'max_session_loss_lamports', 'max_order_fee_lamports'):
        value = cfg.get(key)
        if type(value) not in (str, int) or not re.fullmatch(r'[0-9]{1,40}', str(value)):
            raise ConfigurationError('invalid_raw_config:' + key)
    if int(cfg['max_order_fee_lamports']) <= 0 or int(cfg['max_order_fee_lamports']) > int(cfg['reserve_lamports']):
        raise ConfigurationError('invalid_fee_budget')
    try:
        fraction = Decimal(str(cfg['position_fraction']))
        liquidity = Decimal(str(cfg['min_liquidity_usd']))
        if isinstance(cfg['position_fraction'], bool) or isinstance(cfg['min_liquidity_usd'], bool) or not fraction.is_finite() or not liquidity.is_finite() or not 0 < fraction <= 1 or liquidity <= 0 or int(cfg['max_session_loss_lamports']) <= 0:
            raise ConfigurationError('invalid_risk_config')
    except (KeyError, InvalidOperation):
        raise ConfigurationError('invalid_risk_config') from None
    if not isinstance(cfg.get('variant'), str) or not cfg['variant'] or not isinstance(cfg.get('rpc_url'), str) or not cfg['rpc_url'].startswith(('https://', 'http://')):
        raise ConfigurationError('invalid_source_config')
    return cfg


def load_execution_config(path=CONFIG_PATH):
    def reject_constant(_):
        raise ConfigurationError('nonfinite_config')
    cfg = json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
    return validate_execution_config(cfg)
