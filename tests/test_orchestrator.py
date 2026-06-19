from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.orchestrator import (
    DailyOrchestrator,
    dry_run_executor,
    execute_approved_dry_run,
    execute_candidates_dry_run,
    execute_final_approved_dry_run,
    make_idempotency_key,
    signal_to_order_candidate,
)
from turtle_invest.storage import SQLiteStore
from turtle_invest.strategy import SignalAction, SignalReason, StrategySignal


def buy_signal() -> StrategySignal:
    return StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        reason=SignalReason.ENTRY_BREAKOUT,
        exchange="NASD",
        quantity=10,
        reference_price=200.0,
        atr=2.0,
        threshold=198.0,
        units_after=1,
        message="breakout",
    )


class OrchestratorTests(unittest.TestCase):
    def test_make_idempotency_key_is_stable(self) -> None:
        self.assertEqual(
            make_idempotency_key("2026-06-11", buy_signal()),
            "2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
        )

    def test_hold_signal_is_not_order_candidate(self) -> None:
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.HOLD,
            reason=SignalReason.NO_ACTION,
            exchange="NASD",
            quantity=0,
            reference_price=0,
            atr=None,
            threshold=None,
            units_after=0,
            message="hold",
        )

        self.assertIsNone(signal_to_order_candidate("2026-06-11", signal))

    def test_record_order_candidates_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            orchestrator = DailyOrchestrator(store)

            first = orchestrator.record_order_candidates("2026-06-11", [buy_signal()])
            second = orchestrator.record_order_candidates("2026-06-11", [buy_signal()])

            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 0)

    def test_dry_run_executor_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            orchestrator = DailyOrchestrator(store)
            candidate = orchestrator.record_order_candidates("2026-06-11", [buy_signal()])[0]

            results = orchestrator.execute_approved_candidates(
                [candidate],
                dry_run_executor,
                "2026-06-11T13:30:00Z",
            )

            self.assertEqual(results[0].status, "DRY_RUN")

    def test_execute_approved_dry_run_only_uses_approved_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            orchestrator = DailyOrchestrator(store)
            candidate = orchestrator.record_order_candidates("2026-06-11", [buy_signal()])[0]
            saved = store.list_unapproved_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")

            results = execute_approved_dry_run(store, "2026-06-11")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].idempotency_key, candidate.idempotency_key)

    def test_execute_approved_dry_run_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            orchestrator = DailyOrchestrator(store)
            orchestrator.record_order_candidates("2026-06-11", [buy_signal()])
            saved = store.list_unapproved_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")

            first = execute_approved_dry_run(store, "2026-06-11")
            second = execute_approved_dry_run(store, "2026-06-11")

            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 0)

    def test_execute_final_approved_dry_run_requires_final_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            orchestrator = DailyOrchestrator(store)
            candidate = orchestrator.record_order_candidates("2026-06-11", [buy_signal()])[0]
            saved = store.list_unapproved_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")

            before_final = execute_final_approved_dry_run(store, "2026-06-11")
            store.record_approval(saved.id, "approved", "2026-06-11T13:01:00Z", "approve final", stage="final")
            after_final = execute_final_approved_dry_run(store, "2026-06-11")

            self.assertEqual(before_final, [])
            self.assertEqual(len(after_final), 1)
            self.assertEqual(after_final[0].idempotency_key, candidate.idempotency_key)

    def test_execute_candidates_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            DailyOrchestrator(store).record_order_candidates("2026-06-11", [buy_signal()])
            candidate = store.list_order_candidates("2026-06-11")[0]

            results = execute_candidates_dry_run(store, [candidate])

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "DRY_RUN")


if __name__ == "__main__":
    unittest.main()
