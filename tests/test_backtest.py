from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from turtle_invest.backtest import BacktestPosition, apply_signal, max_drawdown, run_backtest, save_backtest_result
from turtle_invest.config import StrategyConfig
from turtle_invest.strategy import Candle, SignalAction, SignalReason, StrategySignal


def config() -> StrategyConfig:
    return StrategyConfig(
        risk_per_trade=0.01,
        atr_period=3,
        entry_breakout_days=3,
        exit_breakout_days=2,
        stop_loss_atr_multiple=2.0,
        pyramid_atr_step=0.5,
        max_units_per_symbol=4,
        universe_size=1,
        universe_refresh="yearly",
        symbols=["AAPL"],
        exchange_by_symbol={"AAPL": "NAS"},
    )


class BacktestTests(unittest.TestCase):
    def test_apply_buy_signal_skips_when_cash_is_insufficient(self) -> None:
        position = BacktestPosition("AAPL")
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.BUY,
            reason=SignalReason.ENTRY_BREAKOUT,
            exchange="NASD",
            quantity=20,
            reference_price=100,
            atr=1,
            threshold=99,
            units_after=1,
            message="buy",
        )

        cash, trade = apply_signal(1000, position, signal, 100)

        self.assertEqual(cash, 1000)
        self.assertEqual(position.quantity, 0)
        self.assertIsNone(trade)

    def test_run_backtest_generates_trade(self) -> None:
        candles = [
            Candle("20260101", 10, 11, 9, 10),
            Candle("20260102", 10, 11, 9, 10),
            Candle("20260103", 10, 11, 9, 10),
            Candle("20260104", 10, 11, 9, 10),
            Candle("20260105", 12, 13, 11, 12),
        ]

        result = run_backtest({"AAPL": candles}, config(), 10_000)

        self.assertGreaterEqual(len(result.trades), 1)
        self.assertGreater(result.final_equity, 0)

    def test_max_drawdown(self) -> None:
        self.assertAlmostEqual(max_drawdown([100, 110, 99]), -0.1)

    def test_save_backtest_result(self) -> None:
        candles = [
            Candle("20260101", 10, 11, 9, 10),
            Candle("20260102", 10, 11, 9, 10),
            Candle("20260103", 10, 11, 9, 10),
            Candle("20260104", 10, 11, 9, 10),
            Candle("20260105", 12, 13, 11, 12),
        ]
        result = run_backtest({"AAPL": candles}, config(), 10_000)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backtest.json"

            save_backtest_result(result, str(path))

            self.assertTrue(path.exists())
            self.assertIn("final_equity", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
