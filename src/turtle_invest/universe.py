from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from turtle_invest.config import Settings
from turtle_invest.market_calendar import default_us_trade_date
from turtle_invest.storage import SQLiteStore, StoredUniverseMember


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    exchange: str
    rank: int
    market_cap: Optional[float] = None
    source: str = "config"


@dataclass(frozen=True)
class UniverseRefreshResult:
    universe_date: str
    source: str
    members: list[UniverseMember]
    saved_count: int


def configured_universe(config: Settings) -> list[UniverseMember]:
    members = []
    for index, symbol in enumerate(config.strategy.symbols[: config.strategy.universe_size], start=1):
        members.append(
            UniverseMember(
                symbol=symbol,
                exchange=config.strategy.exchange_by_symbol[symbol],
                rank=index,
                source="config",
            )
        )
    return members


def active_universe(config: Settings, store: SQLiteStore) -> list[UniverseMember]:
    store.initialize()
    stored = store.list_universe_members()
    if not stored:
        return configured_universe(config)
    members = []
    for row in stored[: config.strategy.universe_size]:
        members.append(
            UniverseMember(
                symbol=row.symbol,
                exchange=config.strategy.exchange_by_symbol.get(row.symbol, "NAS"),
                rank=row.rank,
                market_cap=row.market_cap,
                source=row.source,
            )
        )
    return members


def refresh_universe_from_config(
    config: Settings,
    store: SQLiteStore,
    universe_date: Optional[str] = None,
    source: str = "config",
) -> UniverseRefreshResult:
    run_date = universe_date or default_us_trade_date()
    members = [
        UniverseMember(
            symbol=member.symbol,
            exchange=member.exchange,
            rank=member.rank,
            market_cap=member.market_cap,
            source=source,
        )
        for member in configured_universe(config)
    ]
    saved = store.replace_universe_members(
        run_date,
        [
            StoredUniverseMember(
                universe_date=run_date,
                symbol=member.symbol,
                rank=member.rank,
                market_cap=member.market_cap,
                source=member.source,
            )
            for member in members
        ],
    )
    return UniverseRefreshResult(run_date, source, members, saved)
