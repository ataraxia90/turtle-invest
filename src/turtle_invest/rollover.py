from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from turtle_invest.market_calendar import next_trading_day
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate


@dataclass(frozen=True)
class RolloverResult:
    source_date: str
    target_date: str
    candidates_found: int
    candidates_created: int


def rollover_pending_candidates(
    store: SQLiteStore,
    source_date: str,
    target_date: Optional[str] = None,
) -> RolloverResult:
    store.initialize()
    run_target = target_date or next_trading_day(source_date).isoformat()
    candidates = store.list_rollover_order_candidates(source_date)
    created = 0
    for candidate in candidates:
        if store.record_order_candidate(clone_candidate(candidate, run_target)):
            created += 1
    return RolloverResult(
        source_date=source_date,
        target_date=run_target,
        candidates_found=len(candidates),
        candidates_created=created,
    )


def clone_candidate(candidate: StoredOrderCandidate, target_date: str) -> StoredOrderCandidate:
    payload = dict(candidate.payload)
    payload["rollover_from_trade_date"] = candidate.trade_date
    payload["rollover_from_idempotency_key"] = candidate.idempotency_key
    payload["rollover_created_at"] = datetime.now(timezone.utc).isoformat()
    return StoredOrderCandidate(
        trade_date=target_date,
        symbol=candidate.symbol,
        action=candidate.action,
        quantity=candidate.quantity,
        reason=f"ROLLOVER_{candidate.reason}",
        idempotency_key=rollover_idempotency_key(candidate, target_date),
        payload=payload,
    )


def rollover_idempotency_key(candidate: StoredOrderCandidate, target_date: str) -> str:
    return f"{target_date}:ROLLOVER:{candidate.idempotency_key}"
