from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from turtle_invest.broker.kis import HttpRequest, HttpResponse, KISClient, format_price
from turtle_invest.config import BrokerConfig


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.url.endswith("/oauth2/tokenP"):
            return HttpResponse(200, {"access_token": "token-123"})
        return HttpResponse(200, {"rt_cd": "0"})


def broker_config() -> BrokerConfig:
    return BrokerConfig(
        provider="kis",
        mode="paper",
        base_url="https://openapivts.koreainvestment.com:29443",
        account_number="12345678",
        account_product_code="01",
        app_key_env="KIS_APP_KEY",
        app_secret_env="KIS_APP_SECRET",
        token_cache_path="data/test_kis_token.json",
    )


class KISClientTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["KIS_APP_KEY"] = "key"
        os.environ["KIS_APP_SECRET"] = "secret"

    def test_issue_access_token(self) -> None:
        transport = FakeTransport()
        client = KISClient(broker_config(), transport=transport)

        token = client.issue_access_token()

        self.assertEqual(token, "token-123")
        self.assertEqual(transport.requests[0].url, "https://openapivts.koreainvestment.com:29443/oauth2/tokenP")

    def test_balance_uses_paper_tr_id(self) -> None:
        transport = FakeTransport()
        client = KISClient(broker_config(), transport=transport, access_token="token-123")

        client.get_overseas_balance()

        self.assertEqual(transport.requests[0].headers["tr_id"], "VTTS3012R")

    def test_balance_auto_issues_token_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = broker_config()
            config = BrokerConfig(
                provider=config.provider,
                mode=config.mode,
                base_url=config.base_url,
                account_number=config.account_number,
                account_product_code=config.account_product_code,
                app_key_env=config.app_key_env,
                app_secret_env=config.app_secret_env,
                token_cache_path=str(Path(tmp) / "token.json"),
            )
            transport = FakeTransport()
            client = KISClient(config, transport=transport)

            client.get_overseas_balance()

            self.assertEqual(len(transport.requests), 2)
            self.assertTrue(transport.requests[0].url.endswith("/oauth2/tokenP"))
            self.assertEqual(transport.requests[1].headers["authorization"], "Bearer token-123")

    def test_cached_token_prevents_new_token_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = broker_config()
            config = BrokerConfig(
                provider=config.provider,
                mode=config.mode,
                base_url=config.base_url,
                account_number=config.account_number,
                account_product_code=config.account_product_code,
                app_key_env=config.app_key_env,
                app_secret_env=config.app_secret_env,
                token_cache_path=str(Path(tmp) / "token.json"),
            )
            first_transport = FakeTransport()
            KISClient(config, transport=first_transport).issue_access_token()

            second_transport = FakeTransport()
            KISClient(config, transport=second_transport).get_overseas_balance()

            self.assertEqual(len(second_transport.requests), 1)
            self.assertFalse(second_transport.requests[0].url.endswith("/oauth2/tokenP"))

    def test_order_uses_buy_paper_tr_id_and_string_quantity(self) -> None:
        transport = FakeTransport()
        client = KISClient(broker_config(), transport=transport, access_token="token-123")

        client.place_overseas_order("AAPL", "BUY", 3, 201.2)

        request = transport.requests[0]
        self.assertEqual(request.headers["tr_id"], "VTTT1002U")
        self.assertEqual(request.body["ORD_QTY"], "3")
        self.assertEqual(request.body["OVRS_ORD_UNPR"], "201.20")

    def test_format_price_requires_positive_price(self) -> None:
        self.assertEqual(format_price(1), "1.00")


if __name__ == "__main__":
    unittest.main()
