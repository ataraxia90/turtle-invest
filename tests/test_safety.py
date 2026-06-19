from __future__ import annotations

import unittest

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.safety import check_safety


def make_settings(app_env: str, broker_mode: str) -> Settings:
    return Settings(
        app=AppConfig(env=app_env, timezone="Asia/Seoul", log_level="INFO"),
        broker=BrokerConfig(
            provider="kis",
            mode=broker_mode,
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


class SafetyTests(unittest.TestCase):
    def test_live_orders_locked_when_app_is_dry_run(self) -> None:
        status = check_safety(make_settings("dry-run", "live"))

        self.assertTrue(status.broker_live)
        self.assertFalse(status.live_order_enabled)

    def test_live_orders_enabled_only_when_both_live(self) -> None:
        status = check_safety(make_settings("live", "live"))

        self.assertTrue(status.live_order_enabled)


if __name__ == "__main__":
    unittest.main()
