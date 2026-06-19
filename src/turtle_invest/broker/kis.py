from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from turtle_invest.config import BrokerConfig


class KISClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: dict[str, Any]


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        ...


class UrllibTransport:
    def send(self, request: HttpRequest) -> HttpResponse:
        data = None
        if request.body is not None:
            data = json.dumps(request.body).encode("utf-8")

        http_request = urllib.request.Request(
            request.url,
            data=data,
            headers=request.headers,
            method=request.method,
        )
        try:
            with urllib.request.urlopen(http_request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return HttpResponse(response.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            body = json.loads(raw) if raw else {"message": str(exc)}
            return HttpResponse(exc.code, body)


class KISClient:
    def __init__(
        self,
        config: BrokerConfig,
        transport: Optional[HttpTransport] = None,
        access_token: Optional[str] = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()
        self.access_token = access_token

    @property
    def app_key(self) -> str:
        return resolve_secret(self.config.app_key_env)

    @property
    def app_secret(self) -> str:
        return resolve_secret(self.config.app_secret_env)

    def issue_access_token(self) -> str:
        response = self._send(
            HttpRequest(
                method="POST",
                url=self._url("/oauth2/tokenP"),
                headers={"content-type": "application/json; charset=utf-8"},
                body={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
            ),
            requires_auth=False,
        )
        token = response.body.get("access_token")
        if not token:
            raise KISClientError("KIS token response did not include access_token")
        self.access_token = str(token)
        self._save_token(response.body)
        return self.access_token

    def get_overseas_daily_price(
        self,
        symbol: str,
        exchange: str = "NAS",
        period_code: str = "D",
        adjust_price: bool = True,
    ) -> dict[str, Any]:
        query = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "1" if adjust_price else "0",
        }
        query_with_period = dict(query)
        query_with_period["PERD"] = period_code
        return self._get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            tr_id="HHDFS76240000",
            query=query_with_period,
        )

    def get_overseas_balance(self, exchange: str = "NASD", currency: str = "USD") -> dict[str, Any]:
        query = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "OVRS_EXCG_CD": exchange,
            "TR_CRCY_CD": currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return self._get(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            tr_id=self._paper_aware_tr_id("TTTS3012R"),
            query=query,
        )

    def get_overseas_order_fills(
        self,
        start_date: str,
        end_date: str,
        symbol: str = "",
        exchange: str = "",
        side: str = "00",
        fill_status: str = "00",
        sort: str = "DS",
    ) -> dict[str, Any]:
        query = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "PDNO": symbol,
            "ORD_STRT_DT": start_date,
            "ORD_END_DT": end_date,
            "SLL_BUY_DVSN": side,
            "CCLD_NCCS_DVSN": fill_status,
            "OVRS_EXCG_CD": exchange,
            "SORT_SQN": sort,
            "ORD_DT": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "CTX_AREA_NK200": "",
            "CTX_AREA_FK200": "",
        }
        return self._get(
            "/uapi/overseas-stock/v1/trading/inquire-ccnl",
            tr_id=self._paper_aware_tr_id("TTTS3035R"),
            query=query,
        )

    def get_overseas_unfilled_orders(
        self,
        exchange: str = "",
        sort: str = "DS",
    ) -> dict[str, Any]:
        query = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "OVRS_EXCG_CD": exchange,
            "SORT_SQN": sort,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return self._get(
            "/uapi/overseas-stock/v1/trading/inquire-nccs",
            tr_id=self._paper_aware_tr_id("TTTS3018R"),
            query=query,
        )

    def place_overseas_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        exchange: str = "NASD",
        order_type: str = "00",
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise KISClientError("quantity must be positive")
        if side not in {"BUY", "SELL"}:
            raise KISClientError("side must be BUY or SELL")

        tr_id = "TTTT1002U" if side == "BUY" else "TTTT1006U"
        body = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": format_price(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": order_type,
        }
        return self._post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=self._paper_aware_tr_id(tr_id),
            body=body,
        )

    def _get(self, path: str, tr_id: str, query: dict[str, str]) -> dict[str, Any]:
        self._ensure_access_token()
        query_string = urllib.parse.urlencode(query)
        response = self._send(
            HttpRequest(
                method="GET",
                url=f"{self._url(path)}?{query_string}",
                headers=self._headers(tr_id),
            )
        )
        return response.body

    def _post(self, path: str, tr_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_access_token()
        response = self._send(
            HttpRequest(
                method="POST",
                url=self._url(path),
                headers=self._headers(tr_id),
                body=body,
            )
        )
        return response.body

    def _send(self, request: HttpRequest, requires_auth: bool = True) -> HttpResponse:
        if requires_auth and not self.access_token:
            self.issue_access_token()
        response = self.transport.send(request)
        if response.status_code >= 400:
            raise KISClientError(f"KIS API failed: {response.status_code} {response.body}")
        return response

    def _ensure_access_token(self) -> None:
        if not self.access_token:
            cached_token = self._load_cached_token()
            if cached_token:
                self.access_token = cached_token
            else:
                self.issue_access_token()

    def _load_cached_token(self) -> Optional[str]:
        cache_path = Path(self.config.token_cache_path)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("base_url") != self.config.base_url:
            return None
        if payload.get("app_key") != self.app_key:
            return None
        if float(payload.get("expires_at", 0)) <= time.time():
            return None
        token = payload.get("access_token")
        return str(token) if token else None

    def _save_token(self, response_body: dict[str, Any]) -> None:
        cache_path = Path(self.config.token_cache_path)
        if cache_path.parent:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
        expires_in = parse_expires_in(response_body.get("expires_in"))
        payload = {
            "base_url": self.config.base_url,
            "app_key": self.app_key,
            "access_token": self.access_token,
            "expires_at": time.time() + max(expires_in - 60, 60),
        }
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    def _headers(self, tr_id: str) -> dict[str, str]:
        if not self.access_token:
            raise KISClientError("access token is missing")
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _paper_aware_tr_id(self, tr_id: str) -> str:
        if self.config.mode == "paper" and tr_id.startswith("T"):
            return f"V{tr_id[1:]}"
        return tr_id

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"


def resolve_secret(value_or_env_name: str) -> str:
    if not value_or_env_name:
        raise KISClientError("missing required secret value or environment variable name")
    return os.environ.get(value_or_env_name, value_or_env_name)


def format_price(price: float) -> str:
    if price <= 0:
        raise KISClientError("price must be positive")
    return f"{price:.2f}"


def parse_expires_in(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 24 * 60 * 60
