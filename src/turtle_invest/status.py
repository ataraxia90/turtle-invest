from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from turtle_invest.config import Settings
from turtle_invest.market_calendar import default_us_trade_date, is_trading_day, next_trading_day
from turtle_invest.safety import SafetyStatus, check_safety
from turtle_invest.storage import SQLiteStore, create_store


@dataclass(frozen=True)
class RuntimeStatus:
    trade_date: str
    is_trading_day: bool
    next_trading_day: str
    safety: SafetyStatus
    table_counts: dict[str, int]


def get_runtime_status(config: Settings, trade_date: Optional[str] = None) -> RuntimeStatus:
    run_date = trade_date or default_us_trade_date()
    store = create_store(config)
    store.initialize()
    return RuntimeStatus(
        trade_date=run_date,
        is_trading_day=is_trading_day(run_date),
        next_trading_day=next_trading_day(run_date).isoformat(),
        safety=check_safety(config),
        table_counts=count_tables(store),
    )


def count_tables(store: SQLiteStore) -> dict[str, int]:
    tables = [
        "account_snapshots",
        "positions",
        "order_candidates",
        "approvals",
        "order_events",
        "reports",
    ]
    with store.connect() as connection:
        return {
            table: int(row[0] if not isinstance(row := connection.execute(f"SELECT count(*) FROM {table}").fetchone(), dict) else row["count"])
            for table in tables
        }
