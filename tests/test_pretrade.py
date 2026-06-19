from __future__ import annotations

import unittest

from turtle_invest.pretrade import (
    build_pretrade_review_message,
    format_optional_float,
    price_deviation,
    quote_exchange_from_order_exchange,
    validate_candidate,
    validate_approved_candidates,
    validate_candidates,
)
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
import tempfile
from pathlib import Path


def candidate(action: str = "BUY", quantity: int = 10, price: float = 100.0) -> StoredOrderCandidate:
    return StoredOrderCandidate(
        trade_date="2026-06-11",
        symbol="AAPL",
        action=action,
        quantity=quantity,
        reason="ENTRY_BREAKOUT",
        idempotency_key="key",
        payload={"reference_price": price, "exchange": "NASD"},
    )


class PreTradeTests(unittest.TestCase):
    def test_validate_buy_candidate(self) -> None:
        result = validate_candidate(candidate(), cash=2000, held_quantity=0, latest_price=101)

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "ok")

    def test_rejects_insufficient_cash(self) -> None:
        result = validate_candidate(candidate(), cash=500, held_quantity=0, latest_price=100)

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "insufficient cash")

    def test_rejects_insufficient_holdings(self) -> None:
        result = validate_candidate(candidate(action="SELL"), cash=0, held_quantity=5, latest_price=100)

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "insufficient holdings")

    def test_rejects_price_deviation(self) -> None:
        result = validate_candidate(candidate(), cash=2000, held_quantity=0, latest_price=110)

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "latest price deviates too far from approval")

    def test_quote_exchange_mapping(self) -> None:
        self.assertEqual(quote_exchange_from_order_exchange("NASD"), "NAS")
        self.assertEqual(quote_exchange_from_order_exchange("NYSE"), "NYS")
        self.assertEqual(quote_exchange_from_order_exchange("AMEX"), "AMS")

    def test_price_deviation(self) -> None:
        self.assertEqual(price_deviation(100, 103), 0.03)

    def test_validate_approved_candidates_skips_broker_when_no_candidates(self) -> None:
        class ExplodingClient:
            def get_overseas_balance(self):
                raise AssertionError("broker should not be called")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            result = validate_approved_candidates(ExplodingClient(), store, "2026-06-11")

            self.assertEqual(result, [])

    def test_validate_candidates_skips_broker_when_empty(self) -> None:
        class ExplodingClient:
            def get_overseas_balance(self):
                raise AssertionError("broker should not be called")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            result = validate_candidates(ExplodingClient(), store, [])

            self.assertEqual(result, [])

    def test_build_pretrade_review_message(self) -> None:
        validation = validate_candidate(candidate(), cash=2000, held_quantity=0, latest_price=101)

        message = build_pretrade_review_message("2026-06-11", [validation])

        self.assertIn("[터틀][2026-06-11] 최종 사전검증", message)
        self.assertIn("검증 통과: 1/1", message)
        self.assertIn("통과 AAPL BUY", message)

    def test_build_pretrade_review_message_empty(self) -> None:
        message = build_pretrade_review_message("2026-06-11", [])

        self.assertIn("최종 사전검증 대상이 없습니다", message)

    def test_format_optional_float(self) -> None:
        self.assertEqual(format_optional_float(None), "-")
        self.assertEqual(format_optional_float(1.234), "1.23")


if __name__ == "__main__":
    unittest.main()
