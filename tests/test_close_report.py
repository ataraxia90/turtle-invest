from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.close_report import append_local_events_summary, matches_broker_row, parse_rows, reconcile_submitted_order_events
from turtle_invest.storage import SQLiteStore


class CloseReportTests(unittest.TestCase):
    def test_parse_rows_accepts_dict_and_list(self) -> None:
        self.assertEqual(parse_rows({"symbol": "AAPL"}), [{"symbol": "AAPL"}])
        self.assertEqual(parse_rows([{"symbol": "AAPL"}, {"symbol": ""}]), [{"symbol": "AAPL"}])

    def test_parse_rows_skips_empty_values(self) -> None:
        self.assertEqual(parse_rows(None), [])
        self.assertEqual(parse_rows(""), [])
        self.assertEqual(parse_rows({"symbol": ""}), [])

    def test_append_local_events_summary(self) -> None:
        message = append_local_events_summary(
            "report",
            [{"status": "DRY_RUN", "idempotency_key": "2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1"}],
        )

        self.assertIn("<b>로컬 실행 이벤트</b>", message)
        self.assertIn("총 1건", message)
        self.assertIn("DRY_RUN", message)

    def test_matches_broker_row_by_order_id(self) -> None:
        event = {"broker_order_id": "123", "payload_json": "{}"}

        self.assertTrue(matches_broker_row(event, {"ODNO": "123"}))

    def test_matches_broker_row_by_payload_when_order_id_missing(self) -> None:
        event = {
            "broker_order_id": None,
            "payload_json": '{"symbol": "AAPL", "action": "BUY", "quantity": 3}',
        }

        self.assertTrue(matches_broker_row(event, {"pdno": "AAPL", "sll_buy_dvsn_cd": "02", "ord_qty": "3"}))

    def test_reconcile_submitted_order_events_updates_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            store.record_order_event(
                order_candidate_id=None,
                broker_order_id="filled-1",
                status="SUBMITTED",
                occurred_at="2026-06-11T13:00:00Z",
                payload={"symbol": "AAPL", "action": "BUY", "quantity": 1},
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
            )
            store.record_order_event(
                order_candidate_id=None,
                broker_order_id="pending-1",
                status="SUBMITTED",
                occurred_at="2026-06-11T13:00:00Z",
                payload={"symbol": "MSFT", "action": "BUY", "quantity": 1},
                idempotency_key="2026-06-11:MSFT:BUY:ENTRY_BREAKOUT:1",
            )

            result = reconcile_submitted_order_events(
                store,
                "2026-06-11",
                filled_rows=[{"ODNO": "filled-1"}],
                pending_rows=[{"ODNO": "pending-1"}],
            )
            statuses = store.list_order_statuses("2026-06-11")
            event_rows = store.list_order_events_for_trade_date("2026-06-11")

            self.assertEqual(result.filled_updates, 1)
            self.assertEqual(result.pending_updates, 1)
            self.assertEqual([row["status"] for row in event_rows], ["FILLED", "PENDING"])


if __name__ == "__main__":
    unittest.main()
