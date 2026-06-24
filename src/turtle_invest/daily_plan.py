from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from turtle_invest.broker.kis import KISClient
from turtle_invest.cash_management import (
    build_cash_plan_from_signals,
    format_cash_plan,
    parking_quantity_for_config,
    should_fetch_parking_price_for_signals,
)
from turtle_invest.config import Settings
from turtle_invest.market_calendar import default_us_trade_date
from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.orchestrator import DailyOrchestrator
from turtle_invest.portfolio_sync import sync_overseas_balance
from turtle_invest.risk_controls import RiskControlContext, apply_portfolio_risk_limits
from turtle_invest.storage import SQLiteStore, create_store
from turtle_invest.strategy import Position, StrategySignal, evaluate_symbol
from turtle_invest.telegram import build_approval_message
from turtle_invest.universe import active_universe


@dataclass(frozen=True)
class DailyPlanResult:
    trade_date: str
    signals: list[StrategySignal]
    saved_candidates: int
    approval_message: str
    synced_positions: int
    total_equity: float
    cash_plan_message: str = ""


def create_daily_plan(
    config: Settings,
    trade_date: Optional[str] = None,
    equity_override: Optional[float] = None,
) -> DailyPlanResult:
    store = create_store(config)
    client = KISClient(config.broker)
    balance = sync_overseas_balance(client, store)
    total_equity = equity_override if equity_override is not None else balance.total_equity
    run_date = trade_date or default_us_trade_date()
    universe = active_universe(config, store)

    signals: list[StrategySignal] = []
    latest_prices: dict[str, float] = {}
    for member in universe:
        candles = fetch_daily_candles(client, member.symbol, member.exchange)
        if candles:
            latest_prices[member.symbol] = candles[-1].close
        position = load_strategy_position(store, member.symbol)
        signals.append(
            attach_order_exchange(
                evaluate_symbol(
                    symbol=member.symbol,
                    candles=candles,
                    position=position,
                    total_equity=total_equity,
                    config=config.strategy,
                ),
                member.exchange,
            )
        )

    signals = apply_portfolio_risk_limits(
        signals,
        RiskControlContext(
            total_equity=total_equity,
            positions={
                member.symbol: load_strategy_position(store, member.symbol)
                for member in universe
            },
            latest_prices=latest_prices,
        ),
        config.strategy,
    )

    orchestrator = DailyOrchestrator(store)
    saved = orchestrator.record_order_candidates(run_date, signals)
    cash_plan_message = build_cash_plan_message(config, store, client, balance.cash, signals)
    approval_message = append_cash_plan_message(build_approval_message(run_date, signals), cash_plan_message)
    return DailyPlanResult(
        trade_date=run_date,
        signals=signals,
        saved_candidates=len(saved),
        approval_message=approval_message,
        synced_positions=balance.positions_count,
        total_equity=total_equity,
        cash_plan_message=cash_plan_message,
    )


def attach_order_exchange(signal: StrategySignal, quote_exchange: str) -> StrategySignal:
    return replace(signal, exchange=order_exchange_code(quote_exchange))


def order_exchange_code(quote_exchange: str) -> str:
    mapping = {
        "NAS": "NASD",
        "NYS": "NYSE",
        "AMS": "AMEX",
    }
    return mapping.get(quote_exchange, quote_exchange)


def load_strategy_position(store: SQLiteStore, symbol: str) -> Position:
    row = store.get_position(symbol)
    if row is None:
        return Position(symbol=symbol, quantity=0, units=0)
    return Position(
        symbol=symbol,
        quantity=int(row["quantity"]),
        units=int(row["units"]),
        last_entry_price=row["last_entry_price"],
    )


def build_cash_plan_message(
    config: Settings,
    store: SQLiteStore,
    client: KISClient,
    cash: float,
    signals: list[StrategySignal],
) -> str:
    actionable = [signal for signal in signals if signal.quantity > 0]
    if not actionable:
        return ""

    parking_price = None
    if should_fetch_parking_price_for_signals(
        cash,
        actionable,
        config.cash.min_cash_buffer,
        config.cash.parking_buy_threshold,
    ):
        parking_symbol = config.cash.parking_etfs[0]
        parking_exchange = config.strategy.exchange_by_symbol.get(parking_symbol, "NAS")
        candles = fetch_daily_candles(client, parking_symbol, parking_exchange)
        parking_price = candles[-1].close if candles else None

    plan = build_cash_plan_from_signals(
        cash=cash,
        parking_quantity=parking_quantity_for_config(store, config.cash),
        parking_price=parking_price,
        signals=actionable,
        config=config.cash,
    )
    return format_cash_plan(plan)


def append_cash_plan_message(approval_message: str, cash_plan_message: str) -> str:
    if (
        not cash_plan_message
        or "No order candidates today." in approval_message
        or "오늘 주문 후보가 없습니다." in approval_message
        or "상태: 주문 후보 없음" in approval_message
    ):
        return approval_message
    return f"{approval_message}\n\n{cash_plan_message}"
