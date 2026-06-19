from __future__ import annotations

import unittest

import tempfile
from pathlib import Path

from turtle_invest.daily_plan import (
    DailyPlanResult,
    append_cash_plan_message,
    attach_order_exchange,
    default_us_trade_date,
    load_strategy_position,
    order_exchange_code,
)
from turtle_invest.storage import SQLiteStore
from turtle_invest.strategy import SignalAction, SignalReason, StrategySignal


class DailyPlanTests(unittest.TestCase):
    def test_daily_plan_result_shape(self) -> None:
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.HOLD,
            reason=SignalReason.NO_ACTION,
            exchange="",
            quantity=0,
            reference_price=0,
            atr=None,
            threshold=None,
            units_after=0,
            message="hold",
        )

        result = DailyPlanResult(
            trade_date="2026-06-11",
            signals=[signal],
            saved_candidates=0,
            approval_message="No order candidates",
            synced_positions=0,
            total_equity=0,
        )

        self.assertEqual(result.trade_date, "2026-06-11")
        self.assertEqual(result.saved_candidates, 0)

    def test_default_us_trade_date_returns_iso_date(self) -> None:
        value = default_us_trade_date()

        self.assertEqual(len(value), 10)
        self.assertEqual(value[4], "-")
        self.assertEqual(value[7], "-")

    def test_load_strategy_position_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            store.upsert_position("AAPL", 5, 2, 200.0, "2026-06-11T00:00:00Z")

            position = load_strategy_position(store, "AAPL")

            self.assertEqual(position.quantity, 5)
            self.assertEqual(position.units, 2)
            self.assertEqual(position.last_entry_price, 200.0)

    def test_order_exchange_code_maps_quote_exchange(self) -> None:
        self.assertEqual(order_exchange_code("NAS"), "NASD")
        self.assertEqual(order_exchange_code("NYS"), "NYSE")
        self.assertEqual(order_exchange_code("AMS"), "AMEX")

    def test_attach_order_exchange(self) -> None:
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.HOLD,
            reason=SignalReason.NO_ACTION,
            exchange="",
            quantity=0,
            reference_price=0,
            atr=None,
            threshold=None,
            units_after=0,
            message="hold",
        )

        updated = attach_order_exchange(signal, "NAS")

        self.assertEqual(updated.exchange, "NASD")

    def test_append_cash_plan_message(self) -> None:
        message = append_cash_plan_message("approval", "cash plan")

        self.assertEqual(message, "approval\n\ncash plan")

    def test_append_cash_plan_message_skips_empty_or_no_candidates(self) -> None:
        self.assertEqual(append_cash_plan_message("approval", ""), "approval")
        self.assertEqual(
            append_cash_plan_message("[터틀][2026-06-11] 오늘 주문 후보가 없습니다.", "cash plan"),
            "[터틀][2026-06-11] 오늘 주문 후보가 없습니다.",
        )


if __name__ == "__main__":
    unittest.main()
