from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol

from turtle_invest.config import TelegramConfig
from turtle_invest.strategy import StrategySignal


STRATEGY_PREFIX = "[터틀]"


class TelegramClientError(RuntimeError):
    pass


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TelegramRequest:
    method: str
    url: str
    body: dict[str, Any]


@dataclass(frozen=True)
class TelegramResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class TelegramMessage:
    update_id: int
    message_id: int
    chat_id: int
    text: str
    username: Optional[str] = None


class TelegramTransport(Protocol):
    def send(self, request: TelegramRequest) -> TelegramResponse:
        ...


class TelegramUrllibTransport:
    def send(self, request: TelegramRequest) -> TelegramResponse:
        data = urllib.parse.urlencode(request.body).encode("utf-8")
        http_request = urllib.request.Request(
            request.url,
            data=data,
            headers={"content-type": "application/x-www-form-urlencoded"},
            method=request.method,
        )
        try:
            with urllib.request.urlopen(http_request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return TelegramResponse(response.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            body = json.loads(raw) if raw else {"description": str(exc)}
            return TelegramResponse(exc.code, body)


class TelegramClient:
    def __init__(
        self,
        config: TelegramConfig,
        transport: Optional[TelegramTransport] = None,
    ) -> None:
        self.config = config
        self.transport = transport or TelegramUrllibTransport()

    @property
    def bot_token(self) -> str:
        return resolve_secret(self.config.bot_token_env)

    @property
    def chat_id(self) -> str:
        return resolve_secret(self.config.chat_id_env)

    def send_message(self, text: str) -> dict[str, Any]:
        response = self.transport.send(
            TelegramRequest(
                method="POST",
                url=f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                body={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                },
            )
        )
        if response.status_code >= 400 or not response.body.get("ok", False):
            raise TelegramClientError(f"Telegram API failed: {response.status_code} {response.body}")
        return response.body

    def get_updates(self, offset: Optional[int] = None, timeout: int = 0) -> list[TelegramMessage]:
        body: dict[str, Any] = {"timeout": str(timeout)}
        if offset is not None:
            body["offset"] = str(offset)
        response = self.transport.send(
            TelegramRequest(
                method="POST",
                url=f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
                body=body,
            )
        )
        if response.status_code >= 400 or not response.body.get("ok", False):
            raise TelegramClientError(f"Telegram API failed: {response.status_code} {response.body}")
        return parse_updates(response.body)


def build_approval_message(trade_date: str, signals: list[StrategySignal]) -> str:
    executable = [signal for signal in signals if signal.quantity > 0]
    if not executable:
        return f"{STRATEGY_PREFIX}[{trade_date}] 오늘 주문 후보가 없습니다."

    lines = [
        f"{STRATEGY_PREFIX}[{trade_date}] 장전 주문 승인 요청",
        "",
        "응답: 승인/yes, 거절/no, 보류/hold",
        "",
    ]
    for index, signal in enumerate(executable, start=1):
        atr = "-" if signal.atr is None else f"{signal.atr:.4f}"
        threshold = "-" if signal.threshold is None else f"{signal.threshold:.2f}"
        lines.extend(
            [
                f"{index}. {signal.symbol} {signal.action.value}",
                f"   거래소: {signal.exchange or '-'}",
                f"   수량: {signal.quantity}",
                f"   사유: {signal.reason.value}",
                f"   기준가: {signal.reference_price:.2f}",
                f"   ATR: {atr}",
                f"   기준선: {threshold}",
                f"   주문 후 유닛: {signal.units_after}",
            ]
        )
    return "\n".join(lines)


def build_close_report(
    report_date: str,
    filled: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> str:
    lines = [
        f"{STRATEGY_PREFIX}[{report_date}] 장마감 보고",
        f"체결: {len(filled)}",
        f"미체결: {len(pending)}",
        f"실패: {len(failed)}",
    ]
    if pending:
        lines.append("")
        lines.append("다음 거래일 재평가 대상:")
        for item in pending:
            lines.append(f"- {item.get('symbol')} {item.get('action')} {item.get('quantity')}")
    if failed:
        lines.append("")
        lines.append("실패 주문:")
        for item in failed:
            lines.append(f"- {item.get('symbol')} {item.get('reason', 'unknown')}")
    return "\n".join(lines)


def parse_approval_response(text: str) -> ApprovalStatus:
    normalized = text.strip().lower()
    if normalized in {"\uc2b9\uc778", "approve", "approved", "yes", "y"}:
        return ApprovalStatus.APPROVED
    if normalized in {"\uac70\uc808", "\ubc18\ub824", "reject", "rejected", "no", "n"}:
        return ApprovalStatus.REJECTED
    if normalized in {"\ubcf4\ub958", "defer", "deferred", "hold"}:
        return ApprovalStatus.DEFERRED
    if normalized in {"승인", "approve", "approved", "yes", "y"}:
        return ApprovalStatus.APPROVED
    if normalized in {"거절", "반려", "reject", "rejected", "no", "n"}:
        return ApprovalStatus.REJECTED
    if normalized in {"보류", "defer", "deferred", "hold"}:
        return ApprovalStatus.DEFERRED
    return ApprovalStatus.UNKNOWN


def parse_updates(payload: dict[str, Any]) -> list[TelegramMessage]:
    if not payload.get("ok", False):
        raise TelegramClientError("Telegram update payload is not ok")

    messages: list[TelegramMessage] = []
    for item in payload.get("result", []):
        message = item.get("message")
        if not message:
            continue
        chat = message.get("chat", {})
        sender = message.get("from", {})
        text = message.get("text")
        if text is None or "id" not in chat or "message_id" not in message:
            continue
        messages.append(
            TelegramMessage(
                update_id=int(item["update_id"]),
                message_id=int(message["message_id"]),
                chat_id=int(chat["id"]),
                text=str(text),
                username=sender.get("username"),
            )
        )
    return messages


def resolve_secret(value_or_env_name: str) -> str:
    if not value_or_env_name:
        raise TelegramClientError("missing required value or environment variable name")
    return os.environ.get(value_or_env_name, value_or_env_name)
