from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.final_pretrade import build_final_pretrade_review
from turtle_invest.storage import SQLiteStore


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


class FinalPreTradeTests(unittest.TestCase):
    def test_build_final_pretrade_review_with_no_candidates_skips_extra_reads(self) -> None:
        class ExplodingClient:
            def get_overseas_balance(self):
                raise AssertionError("broker should not be called without approved candidates")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            store.initialize()

            review = build_final_pretrade_review(settings(db_path), store, ExplodingClient(), "2026-06-11")

        self.assertEqual(review.validations, [])
        self.assertFalse(review.cash_plan_included)
        self.assertIn("<b>[터틀] 최종 사전검증</b>", review.message)
        self.assertIn("상태: 검증 대상 없음", review.message)


if __name__ == "__main__":
    unittest.main()
