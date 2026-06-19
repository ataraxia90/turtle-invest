from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.storage import SQLiteStore, StoredOrderCandidate, StoredUniverseMember


class StorageTests(unittest.TestCase):
    def test_initialize_creates_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.db"
            store = SQLiteStore(db_path)

            store.initialize()

            self.assertTrue(db_path.exists())

    def test_upsert_and_read_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            store.upsert_position("AAPL", 10, 1, 200.0, "2026-06-11T09:00:00Z")
            store.upsert_position("AAPL", 20, 2, 210.0, "2026-06-11T10:00:00Z")
            position = store.get_position("AAPL")

            self.assertIsNotNone(position)
            self.assertEqual(position["quantity"], 20)
            self.assertEqual(position["units"], 2)
            self.assertEqual(position["last_entry_price"], 210.0)

    def test_list_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            store.upsert_position("AAPL", 10, 1, 200.0, "2026-06-11T09:00:00Z")

            self.assertEqual(len(store.list_positions()), 1)

    def test_replace_and_list_universe_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            saved = store.replace_universe_members(
                "2026-01-01",
                [
                    StoredUniverseMember("2026-01-01", "AAPL", 1, 1_000_000.0, "test"),
                    StoredUniverseMember("2026-01-01", "MSFT", 2, None, "test"),
                ],
            )
            members = store.list_universe_members("2026-01-01")

            self.assertEqual(saved, 2)
            self.assertEqual(store.latest_universe_date(), "2026-01-01")
            self.assertEqual([member.symbol for member in members], ["AAPL", "MSFT"])

    def test_order_candidate_idempotency_key_prevents_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            candidate = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="AAPL",
                action="BUY",
                quantity=10,
                reason="ENTRY_BREAKOUT",
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT",
                payload={"reference_price": 200.0},
            )

            self.assertTrue(store.record_order_candidate(candidate))
            self.assertFalse(store.record_order_candidate(candidate))

    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            store.set_state("telegram_update_offset", "100")
            store.set_state("telegram_update_offset", "101")

            self.assertEqual(store.get_state("telegram_update_offset"), "101")

    def test_order_event_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            store.record_order_event(
                order_candidate_id=None,
                broker_order_id=None,
                status="DRY_RUN",
                occurred_at="2026-06-11T13:00:00Z",
                payload={},
                idempotency_key="key-1",
            )

            self.assertTrue(store.has_order_event("key-1"))

    def test_list_order_events_for_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            store.record_order_event(
                order_candidate_id=None,
                broker_order_id=None,
                status="DRY_RUN",
                occurred_at="2026-06-11T13:00:00Z",
                payload={},
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
            )
            store.record_order_event(
                order_candidate_id=None,
                broker_order_id=None,
                status="DRY_RUN",
                occurred_at="2026-06-12T13:00:00Z",
                payload={},
                idempotency_key="2026-06-12:AAPL:BUY:ENTRY_BREAKOUT:1",
            )

            events = store.list_order_events_for_trade_date("2026-06-11")

            self.assertEqual(len(events), 1)

    def test_list_paper_filled_order_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            store.record_order_event(None, None, "PAPER_FILLED", "2026-06-11T13:00:00Z", {}, idempotency_key="a")
            store.record_order_event(None, None, "DRY_RUN", "2026-06-11T13:00:00Z", {}, idempotency_key="b")

            events = store.list_paper_filled_order_events()

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["status"], "PAPER_FILLED")

    def test_list_order_statuses(self) -> None:
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
            saved = store.list_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")
            store.record_approval(saved.id, "approved", "2026-06-11T13:01:00Z", "approve final", stage="final")
            store.record_order_event(
                order_candidate_id=saved.id,
                broker_order_id=None,
                status="DRY_RUN",
                occurred_at="2026-06-11T13:05:00Z",
                payload={},
                idempotency_key=saved.idempotency_key,
            )

            statuses = store.list_order_statuses("2026-06-11")

            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].approval_status, "approved")
            self.assertEqual(statuses[0].final_approval_status, "approved")
            self.assertEqual(statuses[0].event_status, "DRY_RUN")

    def test_final_approval_lists_require_strategy_approval_first(self) -> None:
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
            saved = store.list_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)

            self.assertEqual(store.list_final_unapproved_order_candidates("2026-06-11"), [])
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")

            waiting = store.list_final_unapproved_order_candidates("2026-06-11")

            self.assertEqual(len(waiting), 1)
            store.record_approval(saved.id, "approved", "2026-06-11T13:01:00Z", "approve final", stage="final")
            self.assertEqual(store.list_final_unapproved_order_candidates("2026-06-11"), [])
            self.assertEqual(len(store.list_final_approved_order_candidates("2026-06-11")), 1)

    def test_list_rollover_order_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            pending = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="AAPL",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="pending",
                payload={},
            )
            submitted = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="MSFT",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="submitted",
                payload={},
            )
            failed = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="NVDA",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="failed",
                payload={},
            )
            broker_pending = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="META",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="broker-pending",
                payload={},
            )
            self.assertTrue(store.record_order_candidate(pending))
            self.assertTrue(store.record_order_candidate(submitted))
            self.assertTrue(store.record_order_candidate(failed))
            self.assertTrue(store.record_order_candidate(broker_pending))
            store.record_order_event(None, None, "SUBMITTED", "now", {}, idempotency_key="submitted")
            store.record_order_event(None, None, "FAILED", "now", {}, idempotency_key="failed")
            store.record_order_event(None, None, "PENDING", "now", {}, idempotency_key="broker-pending")

            candidates = store.list_rollover_order_candidates("2026-06-11")

            self.assertEqual([candidate.symbol for candidate in candidates], ["AAPL", "NVDA", "META"])


if __name__ == "__main__":
    unittest.main()
