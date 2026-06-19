from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.storage import SQLiteStore, StoredUniverseMember
from turtle_invest.universe import active_universe, configured_universe, refresh_universe_from_config


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
            atr_period=20,
            entry_breakout_days=20,
            exit_breakout_days=10,
            stop_loss_atr_multiple=2.0,
            pyramid_atr_step=0.5,
            max_units_per_symbol=4,
            universe_size=2,
            universe_refresh="yearly",
            symbols=["AAPL", "MSFT"],
            exchange_by_symbol={"AAPL": "NAS", "MSFT": "NAS", "BRK/B": "NYS"},
        ),
        cash=CashConfig(["SGOV"]),
    )


class UniverseTests(unittest.TestCase):
    def test_configured_universe_uses_strategy_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            members = configured_universe(settings(str(Path(tmp) / "state.db")))

        self.assertEqual([member.symbol for member in members], ["AAPL", "MSFT"])

    def test_refresh_universe_from_config_stores_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = settings(str(Path(tmp) / "state.db"))
            store = SQLiteStore(config.app.database_path)
            store.initialize()

            result = refresh_universe_from_config(config, store, universe_date="2026-01-01")
            stored = store.list_universe_members("2026-01-01")

        self.assertEqual(result.saved_count, 2)
        self.assertEqual([member.symbol for member in stored], ["AAPL", "MSFT"])

    def test_active_universe_prefers_latest_stored_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = settings(str(Path(tmp) / "state.db"))
            store = SQLiteStore(config.app.database_path)
            store.initialize()
            store.replace_universe_members(
                "2026-01-01",
                [
                    StoredUniverseMember("2026-01-01", "BRK/B", 1, None, "test"),
                    StoredUniverseMember("2026-01-01", "AAPL", 2, None, "test"),
                ],
            )

            members = active_universe(config, store)

        self.assertEqual([member.symbol for member in members], ["BRK/B", "AAPL"])
        self.assertEqual(members[0].exchange, "NYS")


if __name__ == "__main__":
    unittest.main()
