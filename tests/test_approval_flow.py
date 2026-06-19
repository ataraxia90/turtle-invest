from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.approval_flow import approval_request_state_key, parse_offset, record_status_for_candidates
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.telegram import ApprovalStatus


class ApprovalFlowTests(unittest.TestCase):
    def test_parse_offset(self) -> None:
        self.assertEqual(parse_offset("123"), 123)
        self.assertIsNone(parse_offset(None))
        self.assertIsNone(parse_offset("bad"))

    def test_approval_request_state_key(self) -> None:
        self.assertEqual(
            approval_request_state_key("2026-06-11"),
            "approval_request_sent:2026-06-11",
        )

    def test_record_status_for_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            candidate = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="AAPL",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
                payload={},
            )
            self.assertTrue(store.record_order_candidate(candidate))
            saved = store.list_unapproved_order_candidates("2026-06-11")

            recorded = record_status_for_candidates(store, saved, ApprovalStatus.APPROVED, "approve")

            self.assertEqual(recorded, 1)
            self.assertEqual(store.list_unapproved_order_candidates("2026-06-11"), [])

    def test_record_final_status_for_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            candidate = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="AAPL",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
                payload={},
            )
            self.assertTrue(store.record_order_candidate(candidate))
            saved = store.list_unapproved_order_candidates("2026-06-11")
            record_status_for_candidates(store, saved, ApprovalStatus.APPROVED, "approve")
            final_waiting = store.list_final_unapproved_order_candidates("2026-06-11")

            recorded = record_status_for_candidates(
                store,
                final_waiting,
                ApprovalStatus.APPROVED,
                "approve final",
                stage="final",
            )

            self.assertEqual(recorded, 1)
            self.assertEqual(len(store.list_final_approved_order_candidates("2026-06-11")), 1)


if __name__ == "__main__":
    unittest.main()
