from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.cash_management import (
    build_cash_plan,
    build_cash_plan_from_signals,
    format_cash_plan,
    parking_quantity_for_config,
    should_fetch_parking_price_for_signals,
)
from turtle_invest.config import CashConfig
from turtle_invest.pretrade import PreTradeValidation
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.strategy import SignalAction, SignalReason, StrategySignal


def validation(action: str, notional: float, ok: bool = True) -> PreTradeValidation:
    return PreTradeValidation(
        candidate=StoredOrderCandidate(
            trade_date="2026-06-11",
            symbol="AAPL",
            action=action,
            quantity=1,
            reason="ENTRY_BREAKOUT",
            idempotency_key=f"{action}:{notional}",
            payload={},
        ),
        ok=ok,
        latest_price=notional,
        approved_price=notional,
        notional=notional,
        message="ok" if ok else "blocked",
    )


class CashManagementTests(unittest.TestCase):
    def test_sells_parking_etf_to_cover_cash_shortfall(self) -> None:
        plan = build_cash_plan(
            cash=500,
            parking_quantity=20,
            parking_price=100,
            validations=[validation("BUY", 1200)],
            config=CashConfig(parking_etfs=["SGOV"], min_cash_buffer=100),
        )

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].action, "SELL")
        self.assertEqual(plan.actions[0].quantity, 8)
        self.assertEqual(plan.available_after_approved_orders, -700)

    def test_caps_parking_etf_sale_at_holdings(self) -> None:
        plan = build_cash_plan(
            cash=0,
            parking_quantity=3,
            parking_price=100,
            validations=[validation("BUY", 1000)],
            config=CashConfig(parking_etfs=["SGOV"]),
        )

        self.assertEqual(plan.actions[0].quantity, 3)

    def test_reports_shortfall_when_price_missing(self) -> None:
        plan = build_cash_plan(
            cash=100,
            parking_quantity=10,
            parking_price=None,
            validations=[validation("BUY", 200)],
            config=CashConfig(parking_etfs=["SGOV"]),
        )

        self.assertEqual(plan.actions, [])
        self.assertIn("가격을 확인할 수 없습니다", plan.message)

    def test_buys_parking_etf_when_surplus_exceeds_threshold(self) -> None:
        plan = build_cash_plan(
            cash=1200,
            parking_quantity=0,
            parking_price=100,
            validations=[],
            config=CashConfig(parking_etfs=["SGOV"], min_cash_buffer=100, parking_buy_threshold=500),
        )

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].action, "BUY")
        self.assertEqual(plan.actions[0].quantity, 11)

    def test_builds_cash_plan_from_strategy_signals(self) -> None:
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.BUY,
            reason=SignalReason.ENTRY_BREAKOUT,
            exchange="NASD",
            quantity=5,
            reference_price=100,
            atr=2,
            threshold=99,
            units_after=1,
            message="entry",
        )

        plan = build_cash_plan_from_signals(
            cash=100,
            parking_quantity=10,
            parking_price=100,
            signals=[signal],
            config=CashConfig(parking_etfs=["SGOV"]),
        )

        self.assertEqual(plan.required_buy_notional, 500)
        self.assertEqual(plan.actions[0].action, "SELL")
        self.assertEqual(plan.actions[0].quantity, 4)

    def test_should_fetch_parking_price_for_signals_when_shortfall(self) -> None:
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.BUY,
            reason=SignalReason.ENTRY_BREAKOUT,
            exchange="NASD",
            quantity=5,
            reference_price=100,
            atr=2,
            threshold=99,
            units_after=1,
            message="entry",
        )

        result = should_fetch_parking_price_for_signals(
            cash=100,
            signals=[signal],
            min_cash_buffer=0,
            parking_buy_threshold=0,
        )

        self.assertTrue(result)

    def test_ignores_blocked_validations(self) -> None:
        plan = build_cash_plan(
            cash=100,
            parking_quantity=10,
            parking_price=100,
            validations=[validation("BUY", 1000, ok=False)],
            config=CashConfig(parking_etfs=["SGOV"]),
        )

        self.assertEqual(plan.required_buy_notional, 0)
        self.assertEqual(plan.actions, [])

    def test_includes_cash_blocked_buy_validations(self) -> None:
        blocked = validation("BUY", 1000, ok=False)
        blocked = PreTradeValidation(
            candidate=blocked.candidate,
            ok=False,
            latest_price=blocked.latest_price,
            approved_price=blocked.approved_price,
            notional=blocked.notional,
            message="insufficient cash",
        )

        plan = build_cash_plan(
            cash=100,
            parking_quantity=10,
            parking_price=100,
            validations=[blocked],
            config=CashConfig(parking_etfs=["SGOV"]),
        )

        self.assertEqual(plan.required_buy_notional, 1000)
        self.assertEqual(plan.actions[0].action, "SELL")
        self.assertEqual(plan.actions[0].quantity, 9)

    def test_formats_cash_plan(self) -> None:
        plan = build_cash_plan(
            cash=100,
            parking_quantity=0,
            parking_price=None,
            validations=[],
            config=CashConfig(parking_etfs=["SGOV"]),
        )

        message = format_cash_plan(plan)

        self.assertIn("<b>[터틀] 현금 점검</b>", message)
        self.assertIn("<b>요약</b>", message)
        self.assertIn("필요 조치: 없음", message)

    def test_parking_quantity_for_config_uses_first_held_etf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            store.upsert_position("SGOV", quantity=7, units=1, last_entry_price=100, updated_at="now")

            quantity = parking_quantity_for_config(store, CashConfig(parking_etfs=["SGOV", "KOFR"]))

        self.assertEqual(quantity, 7)


if __name__ == "__main__":
    unittest.main()
