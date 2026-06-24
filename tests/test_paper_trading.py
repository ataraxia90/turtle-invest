from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.paper_trading import (
    apply_paper_execution,
    build_paper_report_message,
    create_paper_daily_plan,
    execute_paper_candidates,
    initialize_paper_account,
    load_paper_account,
    paper_signal_to_candidate,
    run_paper_day,
)
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.strategy import Candle, SignalAction, SignalReason, StrategySignal


def settings(db_path: str) -> Settings:
    return Settings(
        app=AppConfig(env="dry-run", timezone="Asia/Seoul", log_level="INFO", database_path=db_path),
        broker=BrokerConfig(
            provider="kis",
            mode="live",
            base_url="https://example.com",
            account_number="12345678",
            account_product_code="01",
            app_key_env="KIS_APP_KEY",
            app_secret_env="KIS_APP_SECRET",
        ),
        telegram=TelegramConfig("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        strategy=StrategyConfig(
            risk_per_trade=0.01,
            atr_period=2,
            entry_breakout_days=2,
            exit_breakout_days=2,
            stop_loss_atr_multiple=2.0,
            pyramid_atr_step=0.5,
            max_units_per_symbol=4,
            universe_size=1,
            universe_refresh="yearly",
            symbols=["AAPL"],
            exchange_by_symbol={"AAPL": "NAS"},
        ),
        cash=CashConfig(["SGOV"]),
    )


def candles() -> list[Candle]:
    return [
        Candle("2026-06-10", open=90, high=100, low=89, close=95),
        Candle("2026-06-11", open=95, high=101, low=94, close=100),
        Candle("2026-06-12", open=100, high=102, low=99, close=103),
    ]


def buy_signal() -> StrategySignal:
    return StrategySignal(
        symbol="AAPL",
        action=SignalAction.BUY,
        reason=SignalReason.ENTRY_BREAKOUT,
        exchange="",
        quantity=2,
        reference_price=100,
        atr=5,
        threshold=99,
        units_after=1,
        message="buy",
    )


class PaperTradingTests(unittest.TestCase):
    def test_initialize_and_load_paper_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")

            account = initialize_paper_account(store, cash=10000)
            loaded = load_paper_account(store)

        self.assertEqual(account.cash, 10000)
        self.assertEqual(loaded.cash, 10000)
        self.assertEqual(loaded.positions, {})

    def test_paper_signal_to_candidate_marks_payload(self) -> None:
        candidate = paper_signal_to_candidate("2026-06-15", buy_signal())

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.payload["paper"])
        self.assertIn(":PAPER:", candidate.idempotency_key)

    def test_create_paper_daily_plan_records_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            initialize_paper_account(store, cash=10000)
            with patch("turtle_invest.paper_trading.fetch_daily_candles_for_symbol", return_value=candles()):
                result = create_paper_daily_plan(settings(db_path), store, trade_date="2026-06-15")

            candidates = store.list_order_candidates("2026-06-15")

        self.assertEqual(result.saved_candidates, 1)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].payload["paper"])

    def test_apply_paper_buy_updates_cash_and_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            account = initialize_paper_account(store, cash=1000)
            candidate = StoredOrderCandidate(
                trade_date="2026-06-15",
                symbol="AAPL",
                action="BUY",
                quantity=2,
                reason="PAPER_ENTRY_BREAKOUT",
                idempotency_key="key",
                payload={"units_after": 1},
            )

            execution, updated = apply_paper_execution(account, candidate, price=100)

        self.assertEqual(execution.status, "PAPER_FILLED")
        self.assertEqual(updated.cash, 800)
        self.assertEqual(updated.positions["AAPL"].quantity, 2)

    def test_execute_paper_candidates_records_event_and_persists_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            initialize_paper_account(store, cash=1000)
            candidate = paper_signal_to_candidate("2026-06-15", buy_signal())
            self.assertTrue(store.record_order_candidate(candidate))
            with patch("turtle_invest.paper_trading.fetch_daily_candles_for_symbol", return_value=[
                Candle("2026-06-15", open=101, high=102, low=100, close=101)
            ]):
                result = execute_paper_candidates(settings(db_path), store, trade_date="2026-06-15")

            account = load_paper_account(store)
            events = store.list_order_events_for_trade_date("2026-06-15")

        self.assertEqual(len(result.executions), 1)
        self.assertEqual(result.executions[0].price, 101)
        self.assertEqual(account.positions["AAPL"].quantity, 2)
        self.assertEqual(events[0]["status"], "PAPER_FILLED")

    def test_build_paper_report_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            initialize_paper_account(store, cash=1000)
            candidate = paper_signal_to_candidate("2026-06-15", buy_signal())
            self.assertTrue(store.record_order_candidate(candidate))
            with patch("turtle_invest.paper_trading.fetch_daily_candles_for_symbol", return_value=[
                Candle("2026-06-15", open=101, high=102, low=100, close=101)
            ]):
                plan = create_paper_daily_plan(settings(db_path), store, trade_date="2026-06-15")
                execution = execute_paper_candidates(settings(db_path), store, trade_date="2026-06-15")

            message = build_paper_report_message(plan, execution)

        self.assertIn("<b>[터틀] 모의투자 일일 보고</b>", message)
        self.assertIn("<code>2026-06-15</code>", message)
        self.assertIn("<b>요약</b>", message)
        self.assertIn("평가자산", message)

    def test_run_paper_day_sends_report_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            initialize_paper_account(store, cash=1000)
            with patch("turtle_invest.paper_trading.fetch_daily_candles_for_symbol", return_value=candles()):
                with patch("turtle_invest.telegram.TelegramClient.send_message", return_value={"ok": True}) as send:
                    result = run_paper_day(
                        settings(db_path),
                        store,
                        trade_date="2026-06-15",
                        send_report=True,
                    )

        self.assertTrue(result.sent)
        self.assertEqual(send.call_count, 1)
        self.assertIn("<b>[터틀] 모의투자 일일 보고</b>", send.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
