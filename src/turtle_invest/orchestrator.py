from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.strategy import SignalAction, StrategySignal


@dataclass(frozen=True)
class ExecutionResult:
    idempotency_key: str
    status: str
    broker_order_id: Optional[str] = None
    message: str = ""


OrderExecutor = Callable[[StoredOrderCandidate], ExecutionResult]


def make_idempotency_key(trade_date: str, signal: StrategySignal) -> str:
    return ":".join(
        [
            trade_date,
            signal.symbol,
            signal.action.value,
            signal.reason.value,
            str(signal.units_after),
        ]
    )


def signal_to_order_candidate(trade_date: str, signal: StrategySignal) -> Optional[StoredOrderCandidate]:
    if signal.action == SignalAction.HOLD or signal.quantity <= 0:
        return None
    return StoredOrderCandidate(
        trade_date=trade_date,
        symbol=signal.symbol,
        action=signal.action.value,
        quantity=signal.quantity,
        reason=signal.reason.value,
        idempotency_key=make_idempotency_key(trade_date, signal),
        payload=asdict(signal),
    )


class DailyOrchestrator:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def record_order_candidates(
        self,
        trade_date: str,
        signals: list[StrategySignal],
    ) -> list[StoredOrderCandidate]:
        saved: list[StoredOrderCandidate] = []
        for signal in signals:
            candidate = signal_to_order_candidate(trade_date, signal)
            if candidate is None:
                continue
            if self.store.record_order_candidate(candidate):
                saved.append(candidate)
        return saved

    def execute_approved_candidates(
        self,
        approved_candidates: list[StoredOrderCandidate],
        executor: OrderExecutor,
        occurred_at: str,
    ) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for candidate in approved_candidates:
            if self.store.has_order_event(candidate.idempotency_key):
                continue
            result = executor(candidate)
            self.store.record_order_event(
                order_candidate_id=candidate.id,
                broker_order_id=result.broker_order_id,
                status=result.status,
                occurred_at=occurred_at,
                payload={
                    "idempotency_key": result.idempotency_key,
                    "symbol": candidate.symbol,
                    "action": candidate.action,
                    "quantity": candidate.quantity,
                    "message": result.message,
                },
                idempotency_key=result.idempotency_key,
            )
            results.append(result)
        return results


def dry_run_executor(candidate: StoredOrderCandidate) -> ExecutionResult:
    return ExecutionResult(
        idempotency_key=candidate.idempotency_key,
        status="DRY_RUN",
        broker_order_id=None,
        message="order execution skipped in dry-run mode",
    )


def execute_approved_dry_run(store: SQLiteStore, trade_date: str) -> list[ExecutionResult]:
    candidates = store.list_approved_order_candidates(trade_date)
    return execute_candidates_dry_run(store, candidates)


def execute_final_approved_dry_run(store: SQLiteStore, trade_date: str) -> list[ExecutionResult]:
    candidates = store.list_final_approved_order_candidates(trade_date)
    return execute_candidates_dry_run(store, candidates)


def execute_candidates_dry_run(
    store: SQLiteStore,
    candidates: list[StoredOrderCandidate],
) -> list[ExecutionResult]:
    return DailyOrchestrator(store).execute_approved_candidates(
        approved_candidates=candidates,
        executor=dry_run_executor,
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )
