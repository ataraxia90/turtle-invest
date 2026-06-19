from __future__ import annotations

import unittest

from turtle_invest.config import AppConfig, BrokerConfig, CashConfig, Settings, StrategyConfig, TelegramConfig
from turtle_invest.order_execution import (
    LIVE_ORDER_CONFIRMATION,
    OrderExecutionError,
    execute_final_approved_live_orders,
    extract_reference_price,
    kis_live_order_executor,
)
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
import tempfile
from pathlib import Path


class FakeKISClient:
    def __init__(self, cash: float = 10000.0, latest_price: float = 200.0) -> None:
        self.orders = []
        self.cash = cash
        self.latest_price = latest_price

    def get_overseas_balance(self):
        return {
            "rt_cd": "0",
            "msg1": "ok",
            "output1": [],
            "output2": {"tot_evlu_pfls_amt": self.cash, "frcr_dncl_amt_2": self.cash},
        }

    def get_overseas_daily_price(self, symbol: str, exchange: str):
        return {
            "rt_cd": "0",
            "msg1": "ok",
            "output2": [
                {
                    "xymd": "20260611",
                    "open": self.latest_price,
                    "high": self.latest_price,
                    "low": self.latest_price,
                    "clos": self.latest_price,
                }
            ],
        }

    def place_overseas_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "123"}}


def settings(app_env: str = "dry-run", broker_mode: str = "live") -> Settings:
    return Settings(
        app=AppConfig(env=app_env, timezone="Asia/Seoul", log_level="INFO"),
        broker=BrokerConfig(
            provider="kis",
            mode=broker_mode,
            base_url="https://openapi.koreainvestment.com:9443",
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
    )


class OrderExecutionTests(unittest.TestCase):
    def test_live_executor_refuses_dry_run_app_env(self) -> None:
        with self.assertRaises(OrderExecutionError):
            kis_live_order_executor(settings(app_env="dry-run"), FakeKISClient())  # type: ignore[arg-type]

    def test_live_executor_refuses_paper_broker_mode(self) -> None:
        with self.assertRaises(OrderExecutionError):
            kis_live_order_executor(settings(app_env="live", broker_mode="paper"), FakeKISClient())  # type: ignore[arg-type]

    def test_live_executor_submits_when_explicitly_live(self) -> None:
        client = FakeKISClient()
        executor = kis_live_order_executor(settings(app_env="live"), client)  # type: ignore[arg-type]
        candidate = StoredOrderCandidate(
            trade_date="2026-06-11",
            symbol="AAPL",
            action="BUY",
            quantity=1,
            reason="ENTRY_BREAKOUT",
            idempotency_key="key",
            payload={"reference_price": 200.0, "exchange": "NASD"},
        )

        result = executor(candidate)

        self.assertEqual(result.status, "SUBMITTED")
        self.assertEqual(result.broker_order_id, "123")
        self.assertEqual(client.orders[0]["symbol"], "AAPL")

    def test_extract_reference_price_validates_value(self) -> None:
        self.assertEqual(extract_reference_price({"reference_price": "10.5"}), 10.5)
        with self.assertRaises(OrderExecutionError):
            extract_reference_price({})

    def test_live_final_execution_requires_confirmation_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            with self.assertRaises(OrderExecutionError):
                execute_final_approved_live_orders(
                    settings(app_env="live"),
                    store,
                    FakeKISClient(),  # type: ignore[arg-type]
                    "2026-06-11",
                    confirmation="",
                )

    def test_live_final_execution_refuses_dry_run_app_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()

            with self.assertRaises(OrderExecutionError):
                execute_final_approved_live_orders(
                    settings(app_env="dry-run"),
                    store,
                    FakeKISClient(),  # type: ignore[arg-type]
                    "2026-06-11",
                    confirmation=LIVE_ORDER_CONFIRMATION,
                )

    def test_live_final_execution_uses_final_approved_candidates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            candidate = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="AAPL",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
                payload={"reference_price": 200.0, "exchange": "NASD"},
            )
            self.assertTrue(store.record_order_candidate(candidate))
            saved = store.list_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")
            client = FakeKISClient()

            before_final = execute_final_approved_live_orders(
                settings(app_env="live"),
                store,
                client,  # type: ignore[arg-type]
                "2026-06-11",
                confirmation=LIVE_ORDER_CONFIRMATION,
            )
            store.record_approval(saved.id, "approved", "2026-06-11T13:01:00Z", "approve final", stage="final")
            after_final = execute_final_approved_live_orders(
                settings(app_env="live"),
                store,
                client,  # type: ignore[arg-type]
                "2026-06-11",
                confirmation=LIVE_ORDER_CONFIRMATION,
            )

            self.assertEqual(before_final, [])
            self.assertEqual(len(after_final), 1)
            self.assertEqual(client.orders[0]["symbol"], "AAPL")

    def test_live_final_execution_blocks_failed_preflight_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            candidate = StoredOrderCandidate(
                trade_date="2026-06-11",
                symbol="AAPL",
                action="BUY",
                quantity=1,
                reason="ENTRY_BREAKOUT",
                idempotency_key="2026-06-11:AAPL:BUY:ENTRY_BREAKOUT:1",
                payload={"reference_price": 200.0, "exchange": "NASD"},
            )
            self.assertTrue(store.record_order_candidate(candidate))
            saved = store.list_order_candidates("2026-06-11")[0]
            self.assertIsNotNone(saved.id)
            store.record_approval(saved.id, "approved", "2026-06-11T13:00:00Z", "approve")
            store.record_approval(saved.id, "approved", "2026-06-11T13:01:00Z", "approve final", stage="final")
            client = FakeKISClient(latest_price=300.0)

            with self.assertRaises(OrderExecutionError):
                execute_final_approved_live_orders(
                    settings(app_env="live"),
                    store,
                    client,  # type: ignore[arg-type]
                    "2026-06-11",
                    confirmation=LIVE_ORDER_CONFIRMATION,
                )

            self.assertEqual(client.orders, [])


if __name__ == "__main__":
    unittest.main()
