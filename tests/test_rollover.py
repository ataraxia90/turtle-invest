from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.rollover import clone_candidate, rollover_pending_candidates, rollover_idempotency_key
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate


def candidate() -> StoredOrderCandidate:
    return StoredOrderCandidate(
        trade_date="2026-06-12",
        symbol="AAPL",
        action="BUY",
        quantity=2,
        reason="ENTRY_BREAKOUT",
        idempotency_key="2026-06-12:AAPL:BUY:ENTRY_BREAKOUT:1",
        payload={"reference_price": 200.0},
    )


class RolloverTests(unittest.TestCase):
    def test_clone_candidate_for_target_date(self) -> None:
        cloned = clone_candidate(candidate(), "2026-06-15")

        self.assertEqual(cloned.trade_date, "2026-06-15")
        self.assertEqual(cloned.reason, "ROLLOVER_ENTRY_BREAKOUT")
        self.assertEqual(cloned.payload["rollover_from_trade_date"], "2026-06-12")
        self.assertEqual(
            cloned.idempotency_key,
            rollover_idempotency_key(candidate(), "2026-06-15"),
        )

    def test_rollover_pending_candidates_uses_next_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            self.assertTrue(store.record_order_candidate(candidate()))

            result = rollover_pending_candidates(store, "2026-06-12")

            self.assertEqual(result.target_date, "2026-06-15")
            self.assertEqual(result.candidates_found, 1)
            self.assertEqual(result.candidates_created, 1)
            rolled = store.list_order_candidates("2026-06-15")
            self.assertEqual(len(rolled), 1)
            self.assertEqual(rolled[0].symbol, "AAPL")
            self.assertEqual(store.list_unapproved_order_candidates("2026-06-15")[0].symbol, "AAPL")

    def test_rollover_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            self.assertTrue(store.record_order_candidate(candidate()))

            first = rollover_pending_candidates(store, "2026-06-12")
            second = rollover_pending_candidates(store, "2026-06-12")

            self.assertEqual(first.candidates_created, 1)
            self.assertEqual(second.candidates_created, 0)

    def test_rollover_skips_completed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            self.assertTrue(store.record_order_candidate(candidate()))
            store.record_order_event(None, None, "DRY_RUN", "now", {}, idempotency_key=candidate().idempotency_key)

            result = rollover_pending_candidates(store, "2026-06-12")

            self.assertEqual(result.candidates_found, 0)
            self.assertEqual(result.candidates_created, 0)


if __name__ == "__main__":
    unittest.main()
