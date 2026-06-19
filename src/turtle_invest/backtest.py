from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Optional

from turtle_invest.config import Settings, StrategyConfig
from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.strategy import Candle, Position, SignalAction, StrategySignal, evaluate_symbol
from turtle_invest.storage import SQLiteStore
from turtle_invest.universe import active_universe


@dataclass
class BacktestPosition:
    symbol: str
    quantity: int = 0
    units: int = 0
    last_entry_price: Optional[float] = None
    average_price: float = 0.0
    realized_pnl: float = 0.0

    def to_strategy_position(self) -> Position:
        return Position(
            symbol=self.symbol,
            quantity=self.quantity,
            units=self.units,
            last_entry_price=self.last_entry_price,
        )


@dataclass(frozen=True)
class BacktestTrade:
    date: str
    symbol: str
    action: str
    reason: str
    quantity: int
    price: float
    cash_after: float


@dataclass(frozen=True)
class BacktestResult:
    start_date: str
    end_date: str
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    trades: list[BacktestTrade]
    positions: dict[str, BacktestPosition] = field(default_factory=dict)


def run_backtest(
    candles_by_symbol: dict[str, list[Candle]],
    config: StrategyConfig,
    initial_equity: float,
) -> BacktestResult:
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")

    all_dates = sorted({candle.date for candles in candles_by_symbol.values() for candle in candles})
    if not all_dates:
        raise ValueError("no candles to backtest")

    positions = {symbol: BacktestPosition(symbol=symbol) for symbol in candles_by_symbol}
    cash = initial_equity
    trades: list[BacktestTrade] = []
    equity_curve: list[float] = []

    for current_date in all_dates:
        closes = latest_closes(candles_by_symbol, current_date)
        total_equity = cash + market_value(positions, closes)
        for symbol, candles in candles_by_symbol.items():
            history = [candle for candle in candles if candle.date <= current_date]
            if len(history) < config.atr_period + 1:
                continue
            signal = evaluate_symbol(
                symbol=symbol,
                candles=history,
                position=positions[symbol].to_strategy_position(),
                total_equity=total_equity,
                config=config,
            )
            cash, trade = apply_signal(cash, positions[symbol], signal, history[-1].close)
            if trade is not None:
                trades.append(
                    BacktestTrade(
                        date=current_date,
                        symbol=symbol,
                        action=trade.action,
                        reason=trade.reason,
                        quantity=trade.quantity,
                        price=trade.price,
                        cash_after=cash,
                    )
                )
                closes = latest_closes(candles_by_symbol, current_date)
                total_equity = cash + market_value(positions, closes)
        equity_curve.append(cash + market_value(positions, closes))

    final_equity = equity_curve[-1]
    return BacktestResult(
        start_date=all_dates[0],
        end_date=all_dates[-1],
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=(final_equity / initial_equity) - 1,
        max_drawdown=max_drawdown(equity_curve),
        trades=trades,
        positions=positions,
    )


@dataclass(frozen=True)
class AppliedTrade:
    action: str
    reason: str
    quantity: int
    price: float


def apply_signal(
    cash: float,
    position: BacktestPosition,
    signal: StrategySignal,
    price: float,
) -> tuple[float, Optional[AppliedTrade]]:
    if signal.action == SignalAction.HOLD or signal.quantity <= 0:
        return cash, None

    if signal.action == SignalAction.BUY:
        required_cash = signal.quantity * price
        if cash < required_cash:
            return cash, None
        quantity = signal.quantity
        cost = quantity * price
        previous_cost = position.average_price * position.quantity
        position.quantity += quantity
        position.units = signal.units_after
        position.last_entry_price = price
        position.average_price = (previous_cost + cost) / position.quantity
        return cash - cost, AppliedTrade(signal.action.value, signal.reason.value, quantity, price)

    if signal.action == SignalAction.SELL:
        quantity = min(signal.quantity, position.quantity)
        if quantity <= 0:
            return cash, None
        proceeds = quantity * price
        position.realized_pnl += (price - position.average_price) * quantity
        position.quantity -= quantity
        if position.quantity == 0:
            position.units = 0
            position.last_entry_price = None
            position.average_price = 0.0
        return cash + proceeds, AppliedTrade(signal.action.value, signal.reason.value, quantity, price)

    return cash, None


def latest_closes(candles_by_symbol: dict[str, list[Candle]], current_date: str) -> dict[str, float]:
    closes: dict[str, float] = {}
    for symbol, candles in candles_by_symbol.items():
        eligible = [candle for candle in candles if candle.date <= current_date]
        if eligible:
            closes[symbol] = eligible[-1].close
    return closes


def market_value(positions: dict[str, BacktestPosition], closes: dict[str, float]) -> float:
    return sum(position.quantity * closes.get(symbol, 0.0) for symbol, position in positions.items())


def max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value / peak) - 1)
    return worst


def fetch_universe_candles(client, config: Settings) -> dict[str, list[Candle]]:
    candles_by_symbol: dict[str, list[Candle]] = {}
    for member in active_universe(config, SQLiteStore(config.app.database_path)):
        candles_by_symbol[member.symbol] = fetch_daily_candles(client, member.symbol, member.exchange)
    return candles_by_symbol


def save_backtest_result(result: BacktestResult, path: str) -> None:
    output_path = Path(path)
    if output_path.parent:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "start_date": result.start_date,
        "end_date": result.end_date,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "total_return": result.total_return,
        "max_drawdown": result.max_drawdown,
        "trades": [trade.__dict__ for trade in result.trades],
        "positions": {
            symbol: {
                "quantity": position.quantity,
                "units": position.units,
                "last_entry_price": position.last_entry_price,
                "average_price": position.average_price,
                "realized_pnl": position.realized_pnl,
            }
            for symbol, position in result.positions.items()
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
