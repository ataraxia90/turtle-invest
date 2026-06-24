from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TaxConfig, TelegramConfig
from turtle_invest.storage import SQLiteStore
from turtle_invest.tax_harvest import build_tax_harvest_message, build_tax_harvest_report, parse_price_overrides


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
            universe_size=1,
            universe_refresh="yearly",
            symbols=["AAPL"],
            exchange_by_symbol={"AAPL": "NAS"},
        ),
        cash=CashConfig(["SGOV"]),
        tax=TaxConfig(annual_exemption_krw=2_500_000, harvest_target_krw=2_350_000, usd_krw_fallback=1000),
    )


class TaxHarvestTests(unittest.TestCase):
    def test_build_report_matches_fifo_sales_and_suggests_remaining_gain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            store.initialize()
            store.record_order_event(
                None,
                None,
                "PAPER_FILLED",
                "2026-01-02T14:30:00Z",
                {"paper": True, "symbol": "AAPL", "action": "BUY", "quantity": 10, "price": 100},
                idempotency_key="2026-01-02:PAPER:AAPL:BUY:1",
            )
            store.record_order_event(
                None,
                None,
                "PAPER_FILLED",
                "2026-06-01T14:30:00Z",
                {"paper": True, "symbol": "AAPL", "action": "SELL", "quantity": 2, "price": 200},
                idempotency_key="2026-06-01:PAPER:AAPL:SELL:1",
            )

            report = build_tax_harvest_report(
                settings(db_path),
                store,
                year=2026,
                latest_prices={"AAPL": 250},
                usd_krw=1000,
            )

        self.assertEqual(report.realized_gain_krw, 200_000)
        self.assertEqual(report.remaining_target_krw, 2_150_000)
        self.assertEqual(report.open_lots[0].quantity, 8)
        self.assertEqual(report.candidates[0].suggested_quantity, 8)
        self.assertEqual(report.candidates[0].suggested_gain_krw, 1_200_000)

    def test_build_message_is_prefixed_and_korean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            store = SQLiteStore(db_path)
            store.initialize()

            report = build_tax_harvest_report(settings(db_path), store, year=2026, latest_prices={}, usd_krw=1000)
            message = build_tax_harvest_message(report)

        self.assertIn("<b>[터틀] 세금 점검</b>", message)
        self.assertIn("<code>2026</code>", message)
        self.assertIn("<b>요약</b>", message)

    def test_parse_price_overrides(self) -> None:
        self.assertEqual(parse_price_overrides(["brk/b=500.5"]), {"BRK/B": 500.5})


if __name__ == "__main__":
    unittest.main()
