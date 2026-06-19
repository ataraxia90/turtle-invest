from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.status import count_tables, get_runtime_status
from turtle_invest.storage import SQLiteStore


def make_settings(db_path: str) -> Settings:
    return Settings(
        app=AppConfig(env="dry-run", timezone="Asia/Seoul", log_level="INFO", database_path=db_path),
        broker=BrokerConfig(
            provider="kis",
            mode="live",
            base_url="",
            account_number="",
            account_product_code="01",
            app_key_env="KIS_APP_KEY",
            app_secret_env="KIS_APP_SECRET",
        ),
        telegram=TelegramConfig("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
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
        cash=CashConfig(["SGOV"]),
    )


class StatusTests(unittest.TestCase):
    def test_count_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            counts = count_tables(store)

            self.assertEqual(counts["positions"], 0)
            self.assertIn("reports", counts)

    def test_get_runtime_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = get_runtime_status(make_settings(str(Path(tmp) / "state.db")), "2026-06-12")

            self.assertTrue(status.is_trading_day)
            self.assertFalse(status.safety.live_order_enabled)


if __name__ == "__main__":
    unittest.main()
