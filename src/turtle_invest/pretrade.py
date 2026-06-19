from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.portfolio_sync import BalanceSyncResult, sync_overseas_balance
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.telegram import STRATEGY_PREFIX


@dataclass(frozen=True)
class PreTradeValidation:
    candidate: StoredOrderCandidate
    ok: bool
    latest_price: Optional[float]
    approved_price: Optional[float]
    notional: float
    message: str


def validate_candidate(
    candidate: StoredOrderCandidate,
    cash: float,
    held_quantity: int,
    latest_price: float,
    max_price_deviation: float = 0.03,
) -> PreTradeValidation:
    approved_price = parse_float(candidate.payload.get("reference_price"))
    notional = latest_price * candidate.quantity
    if candidate.quantity <= 0:
        return failed(candidate, latest_price, approved_price, notional, "quantity must be positive")
    if latest_price <= 0:
        return failed(candidate, latest_price, approved_price, notional, "latest price must be positive")
    if approved_price is None or approved_price <= 0:
        return failed(candidate, latest_price, approved_price, notional, "approved reference price is missing")
    if price_deviation(approved_price, latest_price) > max_price_deviation:
        return failed(candidate, latest_price, approved_price, notional, "latest price deviates too far from approval")
    if candidate.action == "BUY" and cash < notional:
        return failed(candidate, latest_price, approved_price, notional, "insufficient cash")
    if candidate.action == "SELL" and held_quantity < candidate.quantity:
        return failed(candidate, latest_price, approved_price, notional, "insufficient holdings")
    if candidate.action not in {"BUY", "SELL"}:
        return failed(candidate, latest_price, approved_price, notional, "unsupported action")
    return PreTradeValidation(candidate, True, latest_price, approved_price, notional, "ok")


def validate_approved_candidates(
    client,
    store: SQLiteStore,
    trade_date: str,
    max_price_deviation: float = 0.03,
) -> list[PreTradeValidation]:
    candidates = store.list_approved_order_candidates(trade_date)
    return validate_candidates(client, store, candidates, max_price_deviation=max_price_deviation)


def validate_candidates(
    client,
    store: SQLiteStore,
    candidates: list[StoredOrderCandidate],
    max_price_deviation: float = 0.03,
) -> list[PreTradeValidation]:
    if not candidates:
        return []
    balance = sync_overseas_balance(client, store)
    validations: list[PreTradeValidation] = []
    for candidate in candidates:
        latest_price = latest_price_for_candidate(client, candidate)
        held_quantity = held_quantity_for_symbol(store, candidate.symbol)
        validations.append(
            validate_candidate(
                candidate=candidate,
                cash=balance.cash,
                held_quantity=held_quantity,
                latest_price=latest_price,
                max_price_deviation=max_price_deviation,
            )
        )
    return validations


def build_pretrade_review_message(trade_date: str, validations: list[PreTradeValidation]) -> str:
    if not validations:
        return f"{STRATEGY_PREFIX}[{trade_date}] 최종 사전검증 대상이 없습니다."

    ok_count = len([validation for validation in validations if validation.ok])
    total_notional = sum(validation.notional for validation in validations if validation.ok)
    lines = [
        f"{STRATEGY_PREFIX}[{trade_date}] 최종 사전검증",
        f"검증 통과: {ok_count}/{len(validations)}",
        f"실행 가능 금액: {total_notional:.2f}",
        "",
        "실주문 실행이 명시적으로 활성화된 경우에만 최종 승인하세요.",
        "",
    ]
    for validation in validations:
        status = "통과" if validation.ok else "차단"
        lines.extend(
            [
                f"{status} {validation.candidate.symbol} {validation.candidate.action}",
                f"  수량: {validation.candidate.quantity}",
                f"  최신가: {format_optional_float(validation.latest_price)}",
                f"  승인 기준가: {format_optional_float(validation.approved_price)}",
                f"  금액: {validation.notional:.2f}",
                f"  메시지: {validation.message}",
            ]
        )
    return "\n".join(lines)


def latest_price_for_candidate(client, candidate: StoredOrderCandidate) -> float:
    quote_exchange = quote_exchange_from_order_exchange(str(candidate.payload.get("exchange", "")))
    candles = fetch_daily_candles(client, candidate.symbol, quote_exchange)
    if not candles:
        return 0.0
    return candles[-1].close


def quote_exchange_from_order_exchange(order_exchange: str) -> str:
    mapping = {
        "NASD": "NAS",
        "NYSE": "NYS",
        "AMEX": "AMS",
    }
    return mapping.get(order_exchange, "NAS")


def held_quantity_for_symbol(store: SQLiteStore, symbol: str) -> int:
    row = store.get_position(symbol)
    if row is None:
        return 0
    return int(row["quantity"])


def price_deviation(reference_price: float, latest_price: float) -> float:
    return abs(latest_price - reference_price) / reference_price


def parse_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_optional_float(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def failed(
    candidate: StoredOrderCandidate,
    latest_price: Optional[float],
    approved_price: Optional[float],
    notional: float,
    message: str,
) -> PreTradeValidation:
    return PreTradeValidation(candidate, False, latest_price, approved_price, notional, message)
