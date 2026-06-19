from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from turtle_invest.broker.kis import KISClient
from turtle_invest.config import Settings
from turtle_invest.orchestrator import DailyOrchestrator, ExecutionResult
from turtle_invest.pretrade import validate_candidates
from turtle_invest.safety import check_safety
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate


class OrderExecutionError(RuntimeError):
    pass


LIVE_ORDER_CONFIRMATION = "I_UNDERSTAND_LIVE_ORDERS"


def execute_final_approved_live_orders(
    config: Settings,
    store: SQLiteStore,
    client: KISClient,
    trade_date: str,
    confirmation: str,
    max_price_deviation: float = 0.03,
) -> list[ExecutionResult]:
    if confirmation != LIVE_ORDER_CONFIRMATION:
        raise OrderExecutionError(f"live orders require confirmation text: {LIVE_ORDER_CONFIRMATION}")

    safety = check_safety(config)
    if not safety.live_order_enabled:
        raise OrderExecutionError(safety.message)

    candidates = store.list_final_approved_order_candidates(trade_date)
    validations = validate_candidates(
        client,
        store,
        candidates,
        max_price_deviation=max_price_deviation,
    )
    blocked = [validation for validation in validations if not validation.ok]
    if blocked:
        summary = "; ".join(
            f"{validation.candidate.symbol} {validation.candidate.action}: {validation.message}"
            for validation in blocked
        )
        raise OrderExecutionError(f"live order preflight validation failed: {summary}")

    return DailyOrchestrator(store).execute_approved_candidates(
        approved_candidates=candidates,
        executor=kis_live_order_executor(config, client),
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )


def kis_live_order_executor(config: Settings, client: KISClient):
    if config.app.env != "live":
        raise OrderExecutionError("live order executor requires app.env=live")
    if config.broker.mode != "live":
        raise OrderExecutionError("live order executor requires broker.mode=live")

    def execute(candidate: StoredOrderCandidate) -> ExecutionResult:
        price = extract_reference_price(candidate.payload)
        response = client.place_overseas_order(
            symbol=candidate.symbol,
            side=candidate.action,
            quantity=candidate.quantity,
            price=price,
            exchange=exchange_from_payload(candidate.payload),
        )
        output = response.get("output") if isinstance(response.get("output"), dict) else {}
        broker_order_id = str(output.get("ODNO") or output.get("odno") or "") or None
        return ExecutionResult(
            idempotency_key=candidate.idempotency_key,
            status="SUBMITTED",
            broker_order_id=broker_order_id,
            message=str(response.get("msg1", "")),
        )

    return execute


def extract_reference_price(payload: dict[str, Any]) -> float:
    value = payload.get("reference_price")
    if value in (None, "", 0):
        raise OrderExecutionError("candidate payload is missing reference_price")
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise OrderExecutionError("candidate reference_price is invalid") from exc
    if price <= 0:
        raise OrderExecutionError("candidate reference_price must be positive")
    return price


def exchange_from_payload(payload: dict[str, Any]) -> str:
    value = payload.get("exchange")
    return str(value) if value else "NASD"
