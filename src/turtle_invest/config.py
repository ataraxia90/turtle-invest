from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Union


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    env: str
    timezone: str
    log_level: str
    database_path: str = "data/turtle_invest.db"
    database_provider: str = "sqlite"
    database_url_env: str = "DATABASE_URL"


@dataclass(frozen=True)
class BrokerConfig:
    provider: str
    mode: str
    base_url: str
    account_number: str
    account_product_code: str
    app_key_env: str
    app_secret_env: str
    token_cache_path: str = "data/kis_token.json"


@dataclass(frozen=True)
class TelegramConfig:
    bot_token_env: str
    chat_id_env: str


@dataclass(frozen=True)
class StrategyConfig:
    risk_per_trade: float
    atr_period: int
    entry_breakout_days: int
    exit_breakout_days: int
    stop_loss_atr_multiple: float
    pyramid_atr_step: float
    max_units_per_symbol: int
    universe_size: int
    universe_refresh: str
    max_new_position_pct: float = 0.15
    max_symbol_position_pct: float = 0.25
    max_equity_exposure_pct: float = 0.95
    max_symbol_stop_risk_pct: float = 0.03
    max_total_stop_risk_pct: float = 0.06
    symbols: list[str] = field(
        default_factory=lambda: [
            "NVDA",
            "AAPL",
            "GOOGL",
            "MSFT",
            "AMZN",
            "META",
            "AVGO",
            "TSLA",
            "BRK/B",
            "LLY",
        ]
    )
    exchange_by_symbol: dict[str, str] = field(
        default_factory=lambda: {
            "NVDA": "NAS",
            "AAPL": "NAS",
            "GOOGL": "NAS",
            "MSFT": "NAS",
            "AMZN": "NAS",
            "META": "NAS",
            "AVGO": "NAS",
            "TSLA": "NAS",
            "BRK/B": "NYS",
            "LLY": "NYS",
        }
    )


@dataclass(frozen=True)
class CashConfig:
    parking_etfs: list[str]
    min_cash_buffer: float = 0.0
    parking_buy_threshold: float = 0.0


@dataclass(frozen=True)
class TaxConfig:
    annual_exemption_krw: float = 2_500_000.0
    harvest_target_krw: float = 2_350_000.0
    usd_krw_fallback: float = 1350.0


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    broker: BrokerConfig
    telegram: TelegramConfig
    strategy: StrategyConfig
    cash: CashConfig
    tax: TaxConfig = field(default_factory=TaxConfig)

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["broker"]["account_number"] = mask_account(data["broker"]["account_number"])
        data["broker"]["app_key_env"] = mask_secret_ref(data["broker"]["app_key_env"])
        data["broker"]["app_secret_env"] = mask_secret_ref(data["broker"]["app_secret_env"])
        data["telegram"]["bot_token_env"] = mask_secret_ref(data["telegram"]["bot_token_env"])
        return data


def load_config(path: Union[str, Path]) -> Settings:
    config_path = Path(path)
    if not config_path.exists():
        fallback = Path("config.example.json")
        if fallback.exists():
            config_path = fallback
        else:
            raise ConfigError(f"config file not found: {path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path}: {exc}") from exc

    return parse_settings(raw)


def parse_settings(raw: dict[str, Any]) -> Settings:
    try:
        settings = Settings(
            app=AppConfig(**raw["app"]),
            broker=BrokerConfig(**raw["broker"]),
            telegram=TelegramConfig(**raw["telegram"]),
            strategy=StrategyConfig(**raw["strategy"]),
            cash=CashConfig(**raw["cash"]),
            tax=TaxConfig(**raw.get("tax", {})),
        )
    except KeyError as exc:
        raise ConfigError(f"missing config section: {exc.args[0]}") from exc
    except TypeError as exc:
        raise ConfigError(f"invalid config shape: {exc}") from exc

    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if settings.app.env not in {"dry-run", "paper", "live"}:
        raise ConfigError("app.env must be one of: dry-run, paper, live")
    if settings.app.database_provider not in {"sqlite", "postgres"}:
        raise ConfigError("app.database_provider must be one of: sqlite, postgres")
    if settings.broker.mode not in {"paper", "live"}:
        raise ConfigError("broker.mode must be one of: paper, live")
    if not 0 < settings.strategy.risk_per_trade <= 0.05:
        raise ConfigError("strategy.risk_per_trade must be between 0 and 0.05")
    if settings.strategy.atr_period <= 0:
        raise ConfigError("strategy.atr_period must be positive")
    if settings.strategy.max_units_per_symbol <= 0:
        raise ConfigError("strategy.max_units_per_symbol must be positive")
    if settings.strategy.universe_size <= 0:
        raise ConfigError("strategy.universe_size must be positive")
    validate_pct(settings.strategy.max_new_position_pct, "strategy.max_new_position_pct")
    validate_pct(settings.strategy.max_symbol_position_pct, "strategy.max_symbol_position_pct")
    validate_pct(settings.strategy.max_equity_exposure_pct, "strategy.max_equity_exposure_pct")
    validate_pct(settings.strategy.max_symbol_stop_risk_pct, "strategy.max_symbol_stop_risk_pct")
    validate_pct(settings.strategy.max_total_stop_risk_pct, "strategy.max_total_stop_risk_pct")
    if not settings.strategy.symbols:
        raise ConfigError("strategy.symbols must not be empty")
    for symbol in settings.strategy.symbols:
        if symbol not in settings.strategy.exchange_by_symbol:
            raise ConfigError(f"missing strategy.exchange_by_symbol entry for: {symbol}")
    if not settings.cash.parking_etfs:
        raise ConfigError("cash.parking_etfs must not be empty")
    if settings.cash.min_cash_buffer < 0:
        raise ConfigError("cash.min_cash_buffer must not be negative")
    if settings.cash.parking_buy_threshold < 0:
        raise ConfigError("cash.parking_buy_threshold must not be negative")
    if settings.tax.annual_exemption_krw <= 0:
        raise ConfigError("tax.annual_exemption_krw must be positive")
    if settings.tax.harvest_target_krw <= 0:
        raise ConfigError("tax.harvest_target_krw must be positive")
    if settings.tax.harvest_target_krw > settings.tax.annual_exemption_krw:
        raise ConfigError("tax.harvest_target_krw must be less than or equal to tax.annual_exemption_krw")
    if settings.tax.usd_krw_fallback <= 0:
        raise ConfigError("tax.usd_krw_fallback must be positive")


def validate_pct(value: float, name: str) -> None:
    if not 0 < value <= 1:
        raise ConfigError(f"{name} must be between 0 and 1")


def mask_account(account_number: str) -> str:
    if not account_number:
        return ""
    if len(account_number) <= 4:
        return "*" * len(account_number)
    return f"{'*' * (len(account_number) - 4)}{account_number[-4:]}"


def mask_secret_ref(value: str) -> str:
    if not value:
        return ""
    if value.isidentifier() and value.upper() == value:
        return value
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 6)}{value[-4:]}"
