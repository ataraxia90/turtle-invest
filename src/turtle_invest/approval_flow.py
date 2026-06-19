from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from turtle_invest.config import Settings
from turtle_invest.daily_plan import create_daily_plan, default_us_trade_date
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate, create_store
from turtle_invest.telegram import (
    ApprovalStatus,
    STRATEGY_PREFIX,
    TelegramClient,
    TelegramClientError,
    parse_approval_response,
)


TELEGRAM_OFFSET_KEY = "telegram_update_offset"
APPROVAL_REQUEST_PREFIX = "approval_request_sent"


@dataclass(frozen=True)
class ApprovalRequestResult:
    trade_date: str
    candidates_count: int
    sent: bool
    message: str


@dataclass(frozen=True)
class ApprovalCollectResult:
    trade_date: str
    updates_seen: int
    approvals_recorded: int
    status: ApprovalStatus
    response_text: str


def request_approval(
    config: Settings,
    trade_date: Optional[str] = None,
    equity_override: Optional[float] = None,
) -> ApprovalRequestResult:
    run_date = trade_date or default_us_trade_date()
    plan = create_daily_plan(config, trade_date=run_date, equity_override=equity_override)
    candidates_count = len([signal for signal in plan.signals if signal.quantity > 0])
    if candidates_count == 0:
        return ApprovalRequestResult(
            trade_date=run_date,
            candidates_count=0,
            sent=False,
            message=f"{STRATEGY_PREFIX}[{run_date}] 오늘 주문 후보가 없습니다.",
        )

    store = create_store(config)
    request_key = approval_request_state_key(run_date)
    if store.get_state(request_key):
        return ApprovalRequestResult(
            trade_date=run_date,
            candidates_count=candidates_count,
            sent=False,
            message=f"{STRATEGY_PREFIX}[{run_date}] 이미 오늘 승인 요청을 보냈습니다.",
        )

    TelegramClient(config.telegram).send_message(plan.approval_message)
    store.set_state(request_key, datetime.now(timezone.utc).isoformat())
    return ApprovalRequestResult(
        trade_date=run_date,
        candidates_count=candidates_count,
        sent=True,
        message=plan.approval_message,
    )


def collect_approval(
    config: Settings,
    trade_date: Optional[str] = None,
    timeout: int = 0,
) -> ApprovalCollectResult:
    run_date = trade_date or default_us_trade_date()
    store = create_store(config)
    store.initialize()
    candidates = store.list_unapproved_order_candidates(run_date)
    return collect_status_for_candidates(
        config=config,
        store=store,
        candidates=candidates,
        trade_date=run_date,
        timeout=timeout,
        stage="strategy",
        empty_message="No unapproved order candidates.",
    )


def collect_final_approval(
    config: Settings,
    trade_date: Optional[str] = None,
    timeout: int = 0,
) -> ApprovalCollectResult:
    run_date = trade_date or default_us_trade_date()
    store = create_store(config)
    store.initialize()
    candidates = store.list_final_unapproved_order_candidates(run_date)
    return collect_status_for_candidates(
        config=config,
        store=store,
        candidates=candidates,
        trade_date=run_date,
        timeout=timeout,
        stage="final",
        empty_message="No candidates waiting for final approval.",
    )


def collect_status_for_candidates(
    config: Settings,
    store: SQLiteStore,
    candidates: list[StoredOrderCandidate],
    trade_date: str,
    timeout: int,
    stage: str,
    empty_message: str,
) -> ApprovalCollectResult:
    if not candidates:
        return ApprovalCollectResult(
            trade_date=trade_date,
            updates_seen=0,
            approvals_recorded=0,
            status=ApprovalStatus.UNKNOWN,
            response_text=empty_message,
        )

    offset = parse_offset(store.get_state(TELEGRAM_OFFSET_KEY))
    client = TelegramClient(config.telegram)
    messages = client.get_updates(offset=offset, timeout=timeout)
    if messages:
        store.set_state(TELEGRAM_OFFSET_KEY, str(max(message.update_id for message in messages) + 1))

    allowed_chat_id = int(client.chat_id)
    for message in messages:
        if message.chat_id != allowed_chat_id:
            continue
        status = parse_approval_response(message.text)
        if status == ApprovalStatus.UNKNOWN:
            continue
        recorded = record_status_for_candidates(store, candidates, status, message.text, stage=stage)
        return ApprovalCollectResult(
            trade_date=trade_date,
            updates_seen=len(messages),
            approvals_recorded=recorded,
            status=status,
            response_text=message.text,
        )

    return ApprovalCollectResult(
        trade_date=trade_date,
        updates_seen=len(messages),
        approvals_recorded=0,
        status=ApprovalStatus.UNKNOWN,
        response_text="No recognized approval response.",
    )


def record_status_for_candidates(
    store: SQLiteStore,
    candidates: list[StoredOrderCandidate],
    status: ApprovalStatus,
    response_text: str,
    stage: str = "strategy",
) -> int:
    recorded = 0
    responded_at = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        if candidate.id is None:
            continue
        store.record_approval(candidate.id, status.value, responded_at, response_text, stage=stage)
        recorded += 1
    return recorded


def parse_offset(value: Optional[str]) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def approval_request_state_key(trade_date: str) -> str:
    return f"{APPROVAL_REQUEST_PREFIX}:{trade_date}"
