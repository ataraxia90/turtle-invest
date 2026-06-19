from __future__ import annotations

from typing import Any, Optional

from turtle_invest.broker.kis import KISClient
from turtle_invest.strategy import Candle


def fetch_daily_candles(client: KISClient, symbol: str, exchange: str = "NAS") -> list[Candle]:
    response = client.get_overseas_daily_price(symbol=symbol, exchange=exchange)
    return parse_daily_candles(response)


def parse_daily_candles(response: dict[str, Any]) -> list[Candle]:
    rows = response.get("output2") or response.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []

    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = first_text(row, ["xymd", "stck_bsop_date", "date"])
        open_price = first_float(row, ["open", "ovrs_nmix_prpr", "stck_oprc"])
        high_price = first_float(row, ["high", "ovrs_nmix_hgpr", "stck_hgpr"])
        low_price = first_float(row, ["low", "ovrs_nmix_lwpr", "stck_lwpr"])
        close_price = first_float(row, ["clos", "close", "ovrs_nmix_prpr", "stck_clpr"])
        volume = int(first_float(row, ["tvol", "volume", "acml_vol"], default=0.0))
        if not date or open_price <= 0 or high_price <= 0 or low_price <= 0 or close_price <= 0:
            continue
        candles.append(
            Candle(
                date=date,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
        )
    return sorted(candles, key=lambda candle: candle.date)


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def first_float(row: dict[str, Any], keys: list[str], default: float = 0.0) -> float:
    for key in keys:
        value = row.get(key)
        parsed = parse_number(value)
        if parsed is not None:
            return parsed
    return default


def parse_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
