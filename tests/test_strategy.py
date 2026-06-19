from __future__ import annotations

import unittest

from turtle_invest.config import StrategyConfig
from turtle_invest.strategy import (
    Candle,
    Position,
    SignalAction,
    SignalReason,
    average_true_range,
    evaluate_symbol,
    unit_size,
)


def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        risk_per_trade=0.01,
        atr_period=20,
        entry_breakout_days=20,
        exit_breakout_days=10,
        stop_loss_atr_multiple=2.0,
        pyramid_atr_step=0.5,
        max_units_per_symbol=4,
        universe_size=10,
        universe_refresh="yearly",
    )


def flat_candles(count: int, close: float = 100.0) -> list[Candle]:
    return [
        Candle(
            date=f"2026-01-{index + 1:02d}",
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000,
        )
        for index in range(count)
    ]


class StrategyTests(unittest.TestCase):
    def test_average_true_range_uses_latest_period(self) -> None:
        candles = flat_candles(21, 100.0)

        self.assertEqual(average_true_range(candles, 20), 2.0)

    def test_unit_size_uses_one_percent_risk_budget(self) -> None:
        self.assertEqual(unit_size(100_000, 0.01, 2.5), 400)

    def test_entry_breakout_generates_buy_signal(self) -> None:
        candles = flat_candles(20, 100.0)
        candles.append(Candle("2026-02-01", 101, 105, 100, 103, 1000))

        signal = evaluate_symbol("AAPL", candles, None, 100_000, strategy_config())

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.reason, SignalReason.ENTRY_BREAKOUT)
        self.assertEqual(signal.quantity, 465)
        self.assertEqual(signal.units_after, 1)

    def test_exit_breakdown_sells_entire_position(self) -> None:
        candles = flat_candles(20, 100.0)
        candles.append(Candle("2026-02-01", 99, 100, 95, 98, 1000))
        position = Position("AAPL", quantity=120, units=2, last_entry_price=90)

        signal = evaluate_symbol("AAPL", candles, position, 100_000, strategy_config())

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.reason, SignalReason.EXIT_BREAKDOWN)
        self.assertEqual(signal.quantity, 120)
        self.assertEqual(signal.units_after, 0)

    def test_stop_loss_takes_priority_over_exit_breakdown(self) -> None:
        candles = flat_candles(20, 100.0)
        candles.append(Candle("2026-02-01", 96, 97, 95, 96, 1000))
        position = Position("AAPL", quantity=120, units=2, last_entry_price=101)

        signal = evaluate_symbol("AAPL", candles, position, 100_000, strategy_config())

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.reason, SignalReason.STOP_LOSS)

    def test_pyramid_entry_adds_one_unit_until_limit(self) -> None:
        candles = flat_candles(20, 100.0)
        candles.append(Candle("2026-02-01", 101, 104, 100, 102, 1000))
        position = Position("AAPL", quantity=100, units=3, last_entry_price=100)

        signal = evaluate_symbol("AAPL", candles, position, 100_000, strategy_config())

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.reason, SignalReason.PYRAMID_ENTRY)
        self.assertEqual(signal.units_after, 4)

    def test_pyramid_entry_stops_at_max_units(self) -> None:
        candles = flat_candles(20, 100.0)
        candles.append(Candle("2026-02-01", 101, 104, 100, 102, 1000))
        position = Position("AAPL", quantity=100, units=4, last_entry_price=100)

        signal = evaluate_symbol("AAPL", candles, position, 100_000, strategy_config())

        self.assertEqual(signal.action, SignalAction.HOLD)


if __name__ == "__main__":
    unittest.main()
