from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Optional

from turtle_invest.config import Settings
from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.storage import SQLiteStore
from turtle_invest.telegram import STRATEGY_PREFIX, format_amount, html_code, html_text


@dataclass(frozen=True)
class TaxLot:
    symbol: str
    quantity: int
    acquisition_price_usd: float
    acquisition_date: str


@dataclass(frozen=True)
class RealizedGain:
    symbol: str
    quantity: int
    sale_price_usd: float
    acquisition_price_usd: float
    realized_gain_usd: float
    realized_gain_krw: float
    sale_date: str


@dataclass(frozen=True)
class TaxHarvestCandidate:
    symbol: str
    quantity: int
    price_usd: float
    cost_basis_usd: float
    unrealized_gain_usd: float
    unrealized_gain_krw: float
    suggested_quantity: int
    suggested_gain_krw: float


@dataclass(frozen=True)
class TaxHarvestReport:
    year: int
    usd_krw: float
    annual_exemption_krw: float
    target_krw: float
    realized_gain_krw: float
    remaining_target_krw: float
    open_lots: list[TaxLot]
    realized: list[RealizedGain]
    candidates: list[TaxHarvestCandidate]


def build_tax_harvest_report(
    config: Settings,
    store: SQLiteStore,
    year: int,
    latest_prices: Optional[dict[str, float]] = None,
    usd_krw: Optional[float] = None,
) -> TaxHarvestReport:
    rate = usd_krw or config.tax.usd_krw_fallback
    lots, realized = build_lot_book(store, year=year, usd_krw=rate)
    prices = latest_prices or fetch_latest_prices(config, lots)
    realized_gain_krw = sum(item.realized_gain_krw for item in realized)
    remaining_target = max(0.0, config.tax.harvest_target_krw - realized_gain_krw)
    candidates = build_harvest_candidates(lots, prices, rate, remaining_target)
    return TaxHarvestReport(
        year=year,
        usd_krw=rate,
        annual_exemption_krw=config.tax.annual_exemption_krw,
        target_krw=config.tax.harvest_target_krw,
        realized_gain_krw=realized_gain_krw,
        remaining_target_krw=remaining_target,
        open_lots=lots,
        realized=realized,
        candidates=candidates,
    )


def build_lot_book(store: SQLiteStore, year: int, usd_krw: float) -> tuple[list[TaxLot], list[RealizedGain]]:
    store.initialize()
    rows = store.list_paper_filled_order_events()
    lots_by_symbol: dict[str, list[TaxLot]] = {}
    realized: list[RealizedGain] = []
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        action = str(payload.get("action", "")).upper()
        symbol = str(payload.get("symbol", ""))
        quantity = parse_int(payload.get("quantity"))
        price = parse_float(payload.get("price"))
        trade_date = trade_date_from_event(row)
        if trade_date[:4].isdigit() and int(trade_date[:4]) > year:
            continue
        if not symbol or quantity <= 0 or price <= 0:
            continue
        if action == "BUY":
            lots_by_symbol.setdefault(symbol, []).append(TaxLot(symbol, quantity, price, trade_date))
        elif action == "SELL":
            realized.extend(match_sale(lots_by_symbol.setdefault(symbol, []), symbol, quantity, price, trade_date, year, usd_krw))
    open_lots = [
        lot
        for symbol in sorted(lots_by_symbol)
        for lot in lots_by_symbol[symbol]
        if lot.quantity > 0
    ]
    return open_lots, realized


def match_sale(
    lots: list[TaxLot],
    symbol: str,
    quantity: int,
    sale_price: float,
    sale_date: str,
    year: int,
    usd_krw: float,
) -> list[RealizedGain]:
    remaining = quantity
    realized: list[RealizedGain] = []
    index = 0
    while remaining > 0 and index < len(lots):
        lot = lots[index]
        matched = min(remaining, lot.quantity)
        gain_usd = (sale_price - lot.acquisition_price_usd) * matched
        if sale_date.startswith(f"{year:04d}-"):
            realized.append(
                RealizedGain(
                    symbol=symbol,
                    quantity=matched,
                    sale_price_usd=sale_price,
                    acquisition_price_usd=lot.acquisition_price_usd,
                    realized_gain_usd=gain_usd,
                    realized_gain_krw=gain_usd * usd_krw,
                    sale_date=sale_date,
                )
            )
        lots[index] = TaxLot(lot.symbol, lot.quantity - matched, lot.acquisition_price_usd, lot.acquisition_date)
        remaining -= matched
        index += 1
    return realized


def build_harvest_candidates(
    lots: list[TaxLot],
    latest_prices: dict[str, float],
    usd_krw: float,
    remaining_target_krw: float,
) -> list[TaxHarvestCandidate]:
    raw_candidates: list[TaxHarvestCandidate] = []
    for lot in lots:
        price = latest_prices.get(lot.symbol)
        if price is None or price <= lot.acquisition_price_usd:
            continue
        gain_per_share_krw = (price - lot.acquisition_price_usd) * usd_krw
        if gain_per_share_krw <= 0:
            continue
        suggested_quantity = min(lot.quantity, int(math.floor(remaining_target_krw / gain_per_share_krw)))
        unrealized_gain_usd = (price - lot.acquisition_price_usd) * lot.quantity
        raw_candidates.append(
            TaxHarvestCandidate(
                symbol=lot.symbol,
                quantity=lot.quantity,
                price_usd=price,
                cost_basis_usd=lot.acquisition_price_usd,
                unrealized_gain_usd=unrealized_gain_usd,
                unrealized_gain_krw=unrealized_gain_usd * usd_krw,
                suggested_quantity=max(0, suggested_quantity),
                suggested_gain_krw=max(0, suggested_quantity) * gain_per_share_krw,
            )
        )
    return sorted(raw_candidates, key=lambda item: item.unrealized_gain_krw, reverse=True)


def fetch_latest_prices(config: Settings, lots: list[TaxLot]) -> dict[str, float]:
    from turtle_invest.broker.kis import KISClient

    client = KISClient(config.broker)
    prices = {}
    for symbol in sorted({lot.symbol for lot in lots}):
        exchange = config.strategy.exchange_by_symbol.get(symbol, "NAS")
        candles = fetch_daily_candles(client, symbol, exchange)
        if candles:
            prices[symbol] = candles[-1].close
    return prices


def build_tax_harvest_message(report: TaxHarvestReport) -> str:
    actionable = [item for item in report.candidates if item.suggested_quantity > 0]
    lines = [
        f"<b>{html_text(STRATEGY_PREFIX)} 세금 점검</b>",
        html_code(report.year),
        "",
        "<b>요약</b>",
        f"적용 환율: {format_amount(report.usd_krw)} KRW/USD",
        f"연간 기본공제: {report.annual_exemption_krw:,.0f}원",
        f"운영 목표: {report.target_krw:,.0f}원",
        f"올해 실현손익: {report.realized_gain_krw:,.0f}원",
        f"목표까지 여유: {report.remaining_target_krw:,.0f}원",
        f"보유 lot: {len(report.open_lots)}",
        f"후보: {len(actionable)}",
    ]
    if actionable:
        lines.append("")
        lines.append("<b>수익실현 후보</b>")
        for index, item in enumerate(actionable, start=1):
            lines.append(
                f"{index}. {html_code(item.symbol)} 보유 {item.quantity}주 / "
                f"후보 {item.suggested_quantity}주 / 예상 실현이익 {item.suggested_gain_krw:,.0f}원"
            )
            lines.append(f"현재가 {format_amount(item.price_usd)} / 원가 {format_amount(item.cost_basis_usd)}")
    else:
        lines.append("")
        lines.append("<b>수익실현 후보</b>")
        lines.append("없음")
    lines.append("")
    lines.append("<b>주의</b>")
    lines.append("세금 계산은 리포트용 추정치입니다. 실제 신고와 증권사 세무자료는 별도로 확인하세요.")
    return "\n".join(lines)


def parse_price_overrides(values: list[str]) -> dict[str, float]:
    prices = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid price override: {value}")
        symbol, raw_price = value.split("=", 1)
        prices[symbol.upper()] = float(raw_price)
    return prices


def trade_date_from_event(row: Any) -> str:
    key = str(row["idempotency_key"] or "")
    if len(key) >= 10 and key[4] == "-" and key[7] == "-":
        return key[:10]
    occurred_at = str(row["occurred_at"])
    return occurred_at[:10]


def parse_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def parse_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0
