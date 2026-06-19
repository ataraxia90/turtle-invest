from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from turtle_invest.approval_flow import record_status_for_candidates
from turtle_invest.orchestrator import execute_approved_dry_run
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.telegram import ApprovalStatus


@dataclass(frozen=True)
class RehearsalResult:
    trade_date: str
    candidate_created: bool
    approvals_recorded: int
    first_execution_count: int
    second_execution_count: int
    idempotency_key: str


def run_local_rehearsal(store: SQLiteStore, trade_date: str) -> RehearsalResult:
    store.initialize()
    candidate = StoredOrderCandidate(
        trade_date=trade_date,
        symbol="REHEARSAL",
        action="BUY",
        quantity=1,
        reason="ENTRY_BREAKOUT",
        idempotency_key=f"{trade_date}:REHEARSAL:BUY:ENTRY_BREAKOUT:1",
        payload={
            "symbol": "REHEARSAL",
            "action": "BUY",
            "reason": "ENTRY_BREAKOUT",
            "quantity": 1,
            "reference_price": 100.0,
            "atr": 2.0,
            "threshold": 99.0,
            "units_after": 1,
            "message": "local rehearsal candidate",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    candidate_created = store.record_order_candidate(candidate)
    candidates = [
        item
        for item in store.list_unapproved_order_candidates(trade_date)
        if item.idempotency_key == candidate.idempotency_key
    ]
    approvals_recorded = record_status_for_candidates(
        store,
        candidates,
        ApprovalStatus.APPROVED,
        "local rehearsal approval",
    )
    first_execution = execute_approved_dry_run(store, trade_date)
    second_execution = execute_approved_dry_run(store, trade_date)
    return RehearsalResult(
        trade_date=trade_date,
        candidate_created=candidate_created,
        approvals_recorded=approvals_recorded,
        first_execution_count=len(
            [result for result in first_execution if result.idempotency_key == candidate.idempotency_key]
        ),
        second_execution_count=len(
            [result for result in second_execution if result.idempotency_key == candidate.idempotency_key]
        ),
        idempotency_key=candidate.idempotency_key,
    )

