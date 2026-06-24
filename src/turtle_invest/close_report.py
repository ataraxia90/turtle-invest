from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from turtle_invest.broker.kis import KISClient
from turtle_invest.config import Settings
from turtle_invest.daily_plan import default_us_trade_date
from turtle_invest.storage import SQLiteStore, create_store
from turtle_invest.telegram import TelegramClient, build_close_report, html_code, html_text


@dataclass(frozen=True)
class CloseReportResult:
    report_date: str
    report_id: int
    filled_count: int
    pending_count: int
    failed_count: int
    sent: bool
    message: str


@dataclass(frozen=True)
class ReconciliationResult:
    filled_updates: int
    pending_updates: int
    unmatched_submitted: int


def create_close_report(
    config: Settings,
    report_date: Optional[str] = None,
    send: bool = False,
    local_only: bool = False,
) -> CloseReportResult:
    run_date = report_date or default_us_trade_date()
    kis_date = run_date.replace("-", "")
    if local_only:
        fills_response: dict[str, Any] = {"local_only": True, "output": []}
        pending_response: dict[str, Any] = {"local_only": True, "output": []}
    else:
        client = KISClient(config.broker)
        fills_response = client.get_overseas_order_fills(start_date=kis_date, end_date=kis_date)
        pending_response = client.get_overseas_unfilled_orders()
    filled = parse_rows(fills_response.get("output"))
    pending = parse_rows(pending_response.get("output"))
    failed: list[dict[str, Any]] = []
    message = build_close_report(run_date, filled=filled, pending=pending, failed=failed)

    store = create_store(config)
    store.initialize()
    reconciliation = reconcile_submitted_order_events(store, run_date, filled, pending)
    local_events = [
        dict(row)
        for row in store.list_order_events_for_trade_date(run_date)
    ]
    if local_events:
        message = append_local_events_summary(message, local_events)
    sent_at = None
    if send:
        TelegramClient(config.telegram).send_message(message)
        sent_at = datetime.now(timezone.utc).isoformat()
    report_id = store.record_report(
        report_date=run_date,
        report_type="market_close",
        payload={
            "fills_response": fills_response,
            "pending_response": pending_response,
            "local_order_events": local_events,
            "filled_count": len(filled),
            "pending_count": len(pending),
            "failed_count": len(failed),
            "local_order_event_count": len(local_events),
            "reconciliation": {
                "filled_updates": reconciliation.filled_updates,
                "pending_updates": reconciliation.pending_updates,
                "unmatched_submitted": reconciliation.unmatched_submitted,
            },
        },
        sent_at=sent_at,
    )
    return CloseReportResult(
        report_date=run_date,
        report_id=report_id,
        filled_count=len(filled),
        pending_count=len(pending),
        failed_count=len(failed),
        sent=send,
        message=message,
    )


def parse_rows(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        return [value] if has_meaningful_value(value) else []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict) and has_meaningful_value(row)]
    return []


def has_meaningful_value(row: dict[str, Any]) -> bool:
    return any(value not in (None, "") for value in row.values())


def append_local_events_summary(message: str, local_events: list[dict[str, Any]]) -> str:
    lines = [message, "", "<b>로컬 실행 이벤트</b>", f"총 {len(local_events)}건"]
    for index, event in enumerate(local_events, start=1):
        lines.append(
            f"{index}. {html_text(event.get('status', '-'))} "
            f"{html_code(event.get('idempotency_key', '-'))}"
        )
    return "\n".join(lines)


def reconcile_submitted_order_events(
    store: SQLiteStore,
    report_date: str,
    filled_rows: list[dict[str, Any]],
    pending_rows: list[dict[str, Any]],
) -> ReconciliationResult:
    submitted = [dict(row) for row in store.list_submitted_order_events_for_trade_date(report_date)]
    filled_updates = 0
    pending_updates = 0
    unmatched = 0
    occurred_at = datetime.now(timezone.utc).isoformat()
    for event in submitted:
        if matches_any_broker_row(event, filled_rows):
            if store.update_order_event_status(
                str(event["idempotency_key"]),
                "FILLED",
                occurred_at,
                reconciliation_payload(event, filled_rows, "FILLED"),
            ):
                filled_updates += 1
        elif matches_any_broker_row(event, pending_rows):
            if store.update_order_event_status(
                str(event["idempotency_key"]),
                "PENDING",
                occurred_at,
                reconciliation_payload(event, pending_rows, "PENDING"),
            ):
                pending_updates += 1
        else:
            unmatched += 1
    return ReconciliationResult(
        filled_updates=filled_updates,
        pending_updates=pending_updates,
        unmatched_submitted=unmatched,
    )


def matches_any_broker_row(event: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    return any(matches_broker_row(event, row) for row in rows)


def matches_broker_row(event: dict[str, Any], row: dict[str, Any]) -> bool:
    event_order_id = normalize_text(event.get("broker_order_id"))
    row_order_id = broker_order_id(row)
    if event_order_id and row_order_id:
        return event_order_id == row_order_id

    payload = parse_event_payload(event)
    event_symbol = normalize_text(payload.get("symbol"))
    event_action = normalize_text(payload.get("action"))
    event_quantity = parse_int(payload.get("quantity"))
    row_symbol = broker_symbol(row)
    row_action = broker_action(row)
    row_quantity = broker_quantity(row)
    return (
        bool(event_symbol)
        and event_symbol == row_symbol
        and (not row_action or event_action == row_action)
        and event_quantity > 0
        and event_quantity == row_quantity
    )


def reconciliation_payload(event: dict[str, Any], rows: list[dict[str, Any]], status: str) -> dict[str, Any]:
    return {
        "reconciled_status": status,
        "previous_event": event,
        "broker_rows": rows,
    }


def parse_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def broker_order_id(row: dict[str, Any]) -> str:
    return first_text(row, ["ODNO", "odno", "ord_no", "ordno", "order_no", "order_id"])


def broker_symbol(row: dict[str, Any]) -> str:
    return first_text(row, ["ovrs_pdno", "pdno", "symb", "symbol"])


def broker_action(row: dict[str, Any]) -> str:
    value = first_text(row, ["sll_buy_dvsn_cd", "sll_buy_dvsn_name", "side", "action"])
    mapping = {
        "01": "SELL",
        "02": "BUY",
        "SELL": "SELL",
        "BUY": "BUY",
        "S": "SELL",
        "B": "BUY",
    }
    return mapping.get(value, value)


def broker_quantity(row: dict[str, Any]) -> int:
    return parse_int(first_text(row, ["ord_qty", "ft_ccld_qty", "quantity", "qty"]))


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return normalize_text(value)
    return ""


def normalize_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip().upper()


def parse_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0
