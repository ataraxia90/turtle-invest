from __future__ import annotations

from dataclasses import dataclass, replace
from math import floor
from typing import Optional

from turtle_invest.config import StrategyConfig
from turtle_invest.strategy import Position, SignalAction, SignalReason, StrategySignal


@dataclass(frozen=True)
class RiskControlContext:
    total_equity: float
    positions: dict[str, Position]
    latest_prices: dict[str, float]


def apply_portfolio_risk_limits(
    signals: list[StrategySignal],
    context: RiskControlContext,
    config: StrategyConfig,
) -> list[StrategySignal]:
    if context.total_equity <= 0:
        return [block_buy(signal, "risk limits blocked buy: total equity is not positive") for signal in signals]

    projected_quantities = {
        symbol: position.quantity
        for symbol, position in context.positions.items()
        if position.quantity > 0
    }
    latest_atr = {
        signal.symbol: signal.atr
        for signal in signals
        if signal.atr is not None and signal.atr > 0
    }

    adjusted: list[StrategySignal] = []
    for signal in signals:
        if signal.action == SignalAction.SELL:
            projected_quantities[signal.symbol] = max(
                0,
                projected_quantities.get(signal.symbol, 0) - signal.quantity,
            )
            adjusted.append(signal)
            continue

        if signal.action != SignalAction.BUY or signal.quantity <= 0:
            adjusted.append(signal)
            continue

        price = signal.reference_price or context.latest_prices.get(signal.symbol, 0.0)
        if price <= 0:
            adjusted.append(block_buy(signal, "risk limits blocked buy: missing price"))
            continue

        current_quantity = projected_quantities.get(signal.symbol, 0)
        current_exposure = exposure_value(projected_quantities, context.latest_prices)
        current_stop_risk = stop_risk_value(projected_quantities, latest_atr, config)
        current_symbol_value = current_quantity * price
        current_symbol_risk = current_quantity * stop_risk_per_share(signal.atr, config)

        max_quantity = signal.quantity
        max_quantity = min(max_quantity, floor((context.total_equity * config.max_new_position_pct) / price))
        max_quantity = min(
            max_quantity,
            floor(max(0.0, context.total_equity * config.max_symbol_position_pct - current_symbol_value) / price),
        )
        max_quantity = min(
            max_quantity,
            floor(max(0.0, context.total_equity * config.max_equity_exposure_pct - current_exposure) / price),
        )

        risk_per_share = stop_risk_per_share(signal.atr, config)
        if risk_per_share > 0:
            max_quantity = min(
                max_quantity,
                floor(max(0.0, context.total_equity * config.max_symbol_stop_risk_pct - current_symbol_risk) / risk_per_share),
            )
            max_quantity = min(
                max_quantity,
                floor(max(0.0, context.total_equity * config.max_total_stop_risk_pct - current_stop_risk) / risk_per_share),
            )

        if max_quantity <= 0:
            adjusted.append(block_buy(signal, "risk limits blocked buy: portfolio caps reached"))
            continue

        capped_quantity = min(signal.quantity, max_quantity)
        projected_quantities[signal.symbol] = current_quantity + capped_quantity
        if capped_quantity < signal.quantity:
            adjusted.append(
                replace(
                    signal,
                    quantity=capped_quantity,
                    message=f"{signal.message}; quantity capped by portfolio risk limits from {signal.quantity} to {capped_quantity}",
                )
            )
        else:
            adjusted.append(signal)
    return adjusted


def exposure_value(quantities: dict[str, int], latest_prices: dict[str, float]) -> float:
    return sum(quantity * latest_prices.get(symbol, 0.0) for symbol, quantity in quantities.items())


def stop_risk_value(
    quantities: dict[str, int],
    atr_by_symbol: dict[str, Optional[float]],
    config: StrategyConfig,
) -> float:
    return sum(
        quantity * stop_risk_per_share(atr_by_symbol.get(symbol), config)
        for symbol, quantity in quantities.items()
    )


def stop_risk_per_share(atr: Optional[float], config: StrategyConfig) -> float:
    if atr is None or atr <= 0:
        return 0.0
    return atr * config.stop_loss_atr_multiple


def block_buy(signal: StrategySignal, message: str) -> StrategySignal:
    if signal.action != SignalAction.BUY:
        return signal
    return replace(
        signal,
        action=SignalAction.HOLD,
        reason=SignalReason.NO_ACTION,
        quantity=0,
        units_after=0,
        message=f"{signal.message}; {message}",
    )
