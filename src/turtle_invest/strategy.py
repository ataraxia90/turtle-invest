from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import floor
from typing import Optional

from turtle_invest.config import StrategyConfig


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalReason(str, Enum):
    ENTRY_BREAKOUT = "ENTRY_BREAKOUT"
    PYRAMID_ENTRY = "PYRAMID_ENTRY"
    EXIT_BREAKDOWN = "EXIT_BREAKDOWN"
    STOP_LOSS = "STOP_LOSS"
    NO_ACTION = "NO_ACTION"


@dataclass(frozen=True)
class Candle:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    units: int
    last_entry_price: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.quantity > 0 and self.units > 0


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    action: SignalAction
    reason: SignalReason
    exchange: str
    quantity: int
    reference_price: float
    atr: Optional[float]
    threshold: Optional[float]
    units_after: int
    message: str


def evaluate_symbol(
    symbol: str,
    candles: list[Candle],
    position: Optional[Position],
    total_equity: float,
    config: StrategyConfig,
    price_point_value: float = 1.0,
) -> StrategySignal:
    if not candles:
        return hold(symbol, "no candles")

    latest = candles[-1]
    current_position = position or Position(symbol=symbol, quantity=0, units=0)
    atr = average_true_range(candles, config.atr_period)

    if current_position.is_open:
        stop_signal = evaluate_stop_loss(symbol, latest, current_position, atr, config)
        if stop_signal is not None:
            return stop_signal

        exit_signal = evaluate_exit_breakdown(symbol, candles, current_position, config)
        if exit_signal is not None:
            return exit_signal

        pyramid_signal = evaluate_pyramid_entry(
            symbol,
            latest,
            current_position,
            total_equity,
            atr,
            config,
            price_point_value,
        )
        if pyramid_signal is not None:
            return pyramid_signal

        return hold(symbol, "open position but no exit or add signal", atr=atr)

    entry_signal = evaluate_entry_breakout(
        symbol,
        candles,
        total_equity,
        atr,
        config,
        price_point_value,
    )
    if entry_signal is not None:
        return entry_signal

    return hold(symbol, "no entry signal", atr=atr)


def evaluate_entry_breakout(
    symbol: str,
    candles: list[Candle],
    total_equity: float,
    atr: Optional[float],
    config: StrategyConfig,
    price_point_value: float,
) -> Optional[StrategySignal]:
    latest = candles[-1]
    threshold = highest_high(candles, config.entry_breakout_days)
    if threshold is None or atr is None:
        return None
    if latest.close <= threshold:
        return None

    quantity = unit_size(total_equity, config.risk_per_trade, atr, price_point_value)
    if quantity <= 0:
        return None

    return StrategySignal(
        symbol=symbol,
        action=SignalAction.BUY,
        reason=SignalReason.ENTRY_BREAKOUT,
        exchange="",
        quantity=quantity,
        reference_price=latest.close,
        atr=atr,
        threshold=threshold,
        units_after=1,
        message=f"{symbol} close broke above prior {config.entry_breakout_days}-day high",
    )


def evaluate_exit_breakdown(
    symbol: str,
    candles: list[Candle],
    position: Position,
    config: StrategyConfig,
) -> Optional[StrategySignal]:
    latest = candles[-1]
    threshold = lowest_low(candles, config.exit_breakout_days)
    if threshold is None or latest.close >= threshold:
        return None

    return StrategySignal(
        symbol=symbol,
        action=SignalAction.SELL,
        reason=SignalReason.EXIT_BREAKDOWN,
        exchange="",
        quantity=position.quantity,
        reference_price=latest.close,
        atr=None,
        threshold=threshold,
        units_after=0,
        message=f"{symbol} close broke below prior {config.exit_breakout_days}-day low",
    )


def evaluate_stop_loss(
    symbol: str,
    latest: Candle,
    position: Position,
    atr: Optional[float],
    config: StrategyConfig,
) -> Optional[StrategySignal]:
    if atr is None or position.last_entry_price is None:
        return None

    threshold = position.last_entry_price - (config.stop_loss_atr_multiple * atr)
    if latest.close > threshold:
        return None

    return StrategySignal(
        symbol=symbol,
        action=SignalAction.SELL,
        reason=SignalReason.STOP_LOSS,
        exchange="",
        quantity=position.quantity,
        reference_price=latest.close,
        atr=atr,
        threshold=threshold,
        units_after=0,
        message=f"{symbol} close reached stop loss",
    )


def evaluate_pyramid_entry(
    symbol: str,
    latest: Candle,
    position: Position,
    total_equity: float,
    atr: Optional[float],
    config: StrategyConfig,
    price_point_value: float,
) -> Optional[StrategySignal]:
    if atr is None or position.last_entry_price is None:
        return None
    if position.units >= config.max_units_per_symbol:
        return None

    threshold = position.last_entry_price + (config.pyramid_atr_step * atr)
    if latest.close < threshold:
        return None

    quantity = unit_size(total_equity, config.risk_per_trade, atr, price_point_value)
    if quantity <= 0:
        return None

    return StrategySignal(
        symbol=symbol,
        action=SignalAction.BUY,
        reason=SignalReason.PYRAMID_ENTRY,
        exchange="",
        quantity=quantity,
        reference_price=latest.close,
        atr=atr,
        threshold=threshold,
        units_after=position.units + 1,
        message=f"{symbol} close rose by at least {config.pyramid_atr_step} ATR from last entry",
    )


def average_true_range(candles: list[Candle], period: int) -> Optional[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(candles) < period + 1:
        return None

    ranges = [
        true_range(previous, current)
        for previous, current in zip(candles[-period - 1 : -1], candles[-period:])
    ]
    return sum(ranges) / period


def true_range(previous: Candle, current: Candle) -> float:
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def highest_high(candles: list[Candle], lookback_days: int) -> Optional[float]:
    prior = prior_window(candles, lookback_days)
    if prior is None:
        return None
    return max(candle.high for candle in prior)


def lowest_low(candles: list[Candle], lookback_days: int) -> Optional[float]:
    prior = prior_window(candles, lookback_days)
    if prior is None:
        return None
    return min(candle.low for candle in prior)


def prior_window(candles: list[Candle], lookback_days: int) -> Optional[list[Candle]]:
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if len(candles) < lookback_days + 1:
        return None
    return candles[-lookback_days - 1 : -1]


def unit_size(
    total_equity: float,
    risk_per_trade: float,
    atr: float,
    price_point_value: float = 1.0,
) -> int:
    if total_equity <= 0 or risk_per_trade <= 0 or atr <= 0 or price_point_value <= 0:
        return 0
    return floor((total_equity * risk_per_trade) / (atr * price_point_value))


def hold(symbol: str, message: str, atr: Optional[float] = None) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=SignalAction.HOLD,
        reason=SignalReason.NO_ACTION,
        exchange="",
        quantity=0,
        reference_price=0,
        atr=atr,
        threshold=None,
        units_after=0,
        message=message,
    )
