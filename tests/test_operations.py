from __future__ import annotations

import unittest

import tempfile
from pathlib import Path

from turtle_invest.operations import (
    FinalPreTradeResult,
    MarketCloseResult,
    PostMarketResult,
    PreMarketResult,
    run_final_pretrade,
    run_post_market,
)
from turtle_invest.telegram import ApprovalStatus
from turtle_invest.approval_flow import ApprovalCollectResult, ApprovalRequestResult
from turtle_invest.close_report import CloseReportResult
from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.final_pretrade import FinalPreTradeReview
from turtle_invest.rollover import RolloverResult
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate


class OperationsTests(unittest.TestCase):
    def test_pre_market_result_shape(self) -> None:
        result = PreMarketResult(
            trade_date="2026-06-11",
            approval_request=ApprovalRequestResult(
                trade_date="2026-06-11",
                candidates_count=0,
                sent=False,
                message="none",
            ),
            approval_collect=ApprovalCollectResult(
                trade_date="2026-06-11",
                updates_seen=0,
                approvals_recorded=0,
                status=ApprovalStatus.UNKNOWN,
                response_text="none",
            ),
        )

        self.assertEqual(result.trade_date, "2026-06-11")
        self.assertFalse(result.approval_request.sent)

    def test_market_close_result_shape(self) -> None:
        result = MarketCloseResult(
            report_date="2026-06-11",
            execution_results=[],
            close_report=CloseReportResult(
                report_date="2026-06-11",
                report_id=1,
                filled_count=0,
                pending_count=0,
                failed_count=0,
                sent=False,
                message="report",
            ),
        )

        self.assertEqual(result.close_report.report_id, 1)
        self.assertEqual(result.execution_results, [])

    def test_final_pretrade_result_shape(self) -> None:
        result = FinalPreTradeResult(
            trade_date="2026-06-11",
            review=FinalPreTradeReview(
                trade_date="2026-06-11",
                validations=[],
                message="review",
                cash_plan_included=False,
            ),
            sent=False,
            final_collect=None,
        )

        self.assertEqual(result.review.message, "review")

    def test_post_market_result_shape(self) -> None:
        market_close = MarketCloseResult(
            report_date="2026-06-11",
            execution_results=[],
            close_report=CloseReportResult(
                report_date="2026-06-11",
                report_id=1,
                filled_count=0,
                pending_count=0,
                failed_count=0,
                sent=False,
                message="report",
            ),
        )
        result = PostMarketResult(
            report_date="2026-06-11",
            market_close=market_close,
            rollover=RolloverResult("2026-06-11", "2026-06-12", 0, 0),
        )

        self.assertEqual(result.rollover.target_date, "2026-06-12")

    def test_final_pretrade_skips_non_trading_day(self) -> None:
        config = Settings(
            app=AppConfig(env="dry-run", timezone="Asia/Seoul", log_level="INFO", database_path=":memory:"),
            broker=BrokerConfig(
                provider="kis",
                mode="live",
                base_url="https://example.com",
                account_number="12345678",
                account_product_code="01",
                app_key_env="KIS_APP_KEY",
                app_secret_env="KIS_APP_SECRET",
            ),
            telegram=TelegramConfig(bot_token_env="TELEGRAM_BOT_TOKEN", chat_id_env="TELEGRAM_CHAT_ID"),
            strategy=StrategyConfig(
                risk_per_trade=0.01,
                atr_period=20,
                entry_breakout_days=20,
                exit_breakout_days=10,
                stop_loss_atr_multiple=2.0,
                pyramid_atr_step=0.5,
                max_units_per_symbol=4,
                universe_size=1,
                universe_refresh="yearly",
                symbols=["AAPL"],
                exchange_by_symbol={"AAPL": "NAS"},
            ),
            cash=CashConfig(parking_etfs=["SGOV"]),
        )

        result = run_final_pretrade(config, trade_date="2026-06-14")

        self.assertFalse(result.sent)
        self.assertIn("not a trading day", result.review.message)

    def test_post_market_skips_non_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_settings(str(Path(tmp) / "state.db"))

            result = run_post_market(config, report_date="2026-06-14", local_only=True)

        self.assertEqual(result.market_close.close_report.report_id, 0)
        self.assertIsNone(result.rollover)

    def test_post_market_rolls_pending_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            config = make_settings(db_path)
            store = SQLiteStore(db_path)
            store.initialize()
            store.record_order_candidate(
                StoredOrderCandidate(
                    trade_date="2026-06-12",
                    symbol="AAPL",
                    action="BUY",
                    quantity=1,
                    reason="ENTRY_BREAKOUT",
                    idempotency_key="2026-06-12:AAPL:BUY:ENTRY_BREAKOUT:1",
                    payload={"reference_price": 200.0},
                )
            )

            result = run_post_market(
                config,
                report_date="2026-06-12",
                force=True,
                local_only=True,
            )

            self.assertIsNotNone(result.rollover)
            self.assertEqual(result.rollover.candidates_created, 1)
            self.assertEqual(len(store.list_order_candidates("2026-06-15")), 1)


def make_settings(db_path: str) -> Settings:
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
        telegram=TelegramConfig(bot_token_env="TELEGRAM_BOT_TOKEN", chat_id_env="TELEGRAM_CHAT_ID"),
        strategy=StrategyConfig(
            risk_per_trade=0.01,
            atr_period=20,
            entry_breakout_days=20,
            exit_breakout_days=10,
            stop_loss_atr_multiple=2.0,
            pyramid_atr_step=0.5,
            max_units_per_symbol=4,
            universe_size=1,
            universe_refresh="yearly",
            symbols=["AAPL"],
            exchange_by_symbol={"AAPL": "NAS"},
        ),
        cash=CashConfig(parking_etfs=["SGOV"]),
    )


if __name__ == "__main__":
    unittest.main()
