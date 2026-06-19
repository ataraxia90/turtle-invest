from __future__ import annotations

import unittest

from turtle_invest.config import ConfigError, mask_account, parse_settings


def valid_config() -> dict:
    return {
        "app": {"env": "dry-run", "timezone": "Asia/Seoul", "log_level": "INFO"},
        "broker": {
            "provider": "kis",
            "mode": "paper",
            "base_url": "https://openapivts.koreainvestment.com:29443",
            "account_number": "1234567890",
            "account_product_code": "01",
            "app_key_env": "KIS_APP_KEY",
            "app_secret_env": "KIS_APP_SECRET",
        },
        "telegram": {
            "bot_token_env": "TELEGRAM_BOT_TOKEN",
            "chat_id_env": "TELEGRAM_CHAT_ID",
        },
        "strategy": {
            "risk_per_trade": 0.01,
            "atr_period": 20,
            "entry_breakout_days": 20,
            "exit_breakout_days": 10,
            "stop_loss_atr_multiple": 2.0,
            "pyramid_atr_step": 0.5,
            "max_units_per_symbol": 4,
            "universe_size": 10,
            "universe_refresh": "yearly",
            "symbols": ["AAPL", "MSFT"],
            "exchange_by_symbol": {"AAPL": "NAS", "MSFT": "NAS"},
        },
        "cash": {"parking_etfs": ["SGOV", "KOFR"]},
    }


class ConfigTests(unittest.TestCase):
    def test_parse_valid_config(self) -> None:
        settings = parse_settings(valid_config())

        self.assertEqual(settings.app.env, "dry-run")
        self.assertEqual(settings.broker.provider, "kis")
        self.assertEqual(settings.strategy.max_units_per_symbol, 4)
        self.assertEqual(settings.strategy.symbols, ["AAPL", "MSFT"])
        self.assertEqual(settings.tax.annual_exemption_krw, 2_500_000)

    def test_rejects_missing_exchange_mapping(self) -> None:
        config = valid_config()
        config["strategy"]["exchange_by_symbol"] = {"AAPL": "NAS"}

        with self.assertRaises(ConfigError):
            parse_settings(config)

    def test_rejects_invalid_environment(self) -> None:
        config = valid_config()
        config["app"]["env"] = "production"

        with self.assertRaises(ConfigError):
            parse_settings(config)

    def test_rejects_invalid_risk_limit_percent(self) -> None:
        config = valid_config()
        config["strategy"]["max_equity_exposure_pct"] = 1.5

        with self.assertRaises(ConfigError):
            parse_settings(config)

    def test_masks_account_number(self) -> None:
        self.assertEqual(mask_account("1234567890"), "******7890")


if __name__ == "__main__":
    unittest.main()
