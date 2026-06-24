from __future__ import annotations

import os
import unittest

from turtle_invest.config import TelegramConfig
from turtle_invest.strategy import SignalAction, SignalReason, StrategySignal
from turtle_invest.telegram import (
    ApprovalStatus,
    TelegramClient,
    TelegramRequest,
    TelegramResponse,
    build_approval_message,
    build_close_report,
    parse_updates,
    parse_approval_response,
)


class FakeTelegramTransport:
    def __init__(self) -> None:
        self.requests: list[TelegramRequest] = []

    def send(self, request: TelegramRequest) -> TelegramResponse:
        self.requests.append(request)
        return TelegramResponse(200, {"ok": True, "result": {"message_id": 1}})


class TelegramTests(unittest.TestCase):
    def test_parse_approval_response(self) -> None:
        self.assertEqual(parse_approval_response("승인"), ApprovalStatus.APPROVED)
        self.assertEqual(parse_approval_response("거절"), ApprovalStatus.REJECTED)
        self.assertEqual(parse_approval_response("보류"), ApprovalStatus.DEFERRED)
        self.assertEqual(parse_approval_response("approve"), ApprovalStatus.APPROVED)
        self.assertEqual(parse_approval_response("reject"), ApprovalStatus.REJECTED)
        self.assertEqual(parse_approval_response("hold"), ApprovalStatus.DEFERRED)
        self.assertEqual(parse_approval_response("later"), ApprovalStatus.UNKNOWN)

    def test_build_approval_message_includes_signal_details(self) -> None:
        signal = StrategySignal(
            symbol="AAPL",
            action=SignalAction.BUY,
            reason=SignalReason.ENTRY_BREAKOUT,
            exchange="NASD",
            quantity=10,
            reference_price=200.0,
            atr=2.5,
            threshold=198.0,
            units_after=1,
            message="breakout",
        )

        message = build_approval_message("2026-06-11", [signal])

        self.assertIn("<b>[터틀] 주문 승인 요청</b>", message)
        self.assertIn("<code>2026-06-11</code>", message)
        self.assertIn("<b>요약</b>", message)
        self.assertIn("종목: <code>AAPL</code>", message)
        self.assertIn("동작: BUY", message)
        self.assertIn("거래소: NASD", message)
        self.assertIn("수량: 10", message)
        self.assertIn("ENTRY_BREAKOUT", message)

    def test_build_close_report_marks_pending_for_next_day(self) -> None:
        message = build_close_report(
            "2026-06-11",
            filled=[],
            pending=[{"symbol": "AAPL", "action": "BUY", "quantity": 10}],
            failed=[],
        )

        self.assertIn("<b>[터틀] 장마감 보고</b>", message)
        self.assertIn("<code>2026-06-11</code>", message)
        self.assertIn("미체결: 1", message)
        self.assertIn("<b>다음 거래일 재평가 대상</b>", message)

    def test_send_message_uses_bot_token_and_chat_id(self) -> None:
        os.environ["TELEGRAM_BOT_TOKEN"] = "token"
        os.environ["TELEGRAM_CHAT_ID"] = "chat"
        transport = FakeTelegramTransport()
        client = TelegramClient(
            TelegramConfig("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
            transport=transport,
        )

        client.send_message("hello")

        request = transport.requests[0]
        self.assertEqual(request.url, "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(request.body["chat_id"], "chat")
        self.assertEqual(request.body["text"], "hello")

    def test_parse_updates_extracts_chat_id_and_text(self) -> None:
        payload = {
            "ok": True,
            "result": [
                {
                    "update_id": 650350128,
                    "message": {
                        "message_id": 1,
                        "from": {"id": 874101939, "is_bot": False, "username": "namkyu_kim"},
                        "chat": {"id": 874101939, "username": "namkyu_kim", "type": "private"},
                        "date": 1781164257,
                        "text": "/start",
                    },
                },
                {
                    "update_id": 650350129,
                    "message": {
                        "message_id": 2,
                        "from": {"id": 874101939, "is_bot": False, "username": "namkyu_kim"},
                        "chat": {"id": 874101939, "username": "namkyu_kim", "type": "private"},
                        "date": 1781164258,
                        "text": "d",
                    },
                },
            ],
        }

        messages = parse_updates(payload)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].chat_id, 874101939)
        self.assertEqual(messages[0].text, "/start")
        self.assertEqual(messages[1].text, "d")


if __name__ == "__main__":
    unittest.main()
