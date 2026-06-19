from __future__ import annotations

from dataclasses import dataclass

from turtle_invest.cash_management import (
    build_cash_plan,
    format_cash_plan,
    parking_quantity_for_config,
    should_fetch_parking_price_for_validations,
)
from turtle_invest.config import Settings
from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.portfolio_sync import sync_overseas_balance
from turtle_invest.pretrade import PreTradeValidation, build_pretrade_review_message, validate_approved_candidates
from turtle_invest.storage import SQLiteStore


@dataclass(frozen=True)
class FinalPreTradeReview:
    trade_date: str
    validations: list[PreTradeValidation]
    message: str
    cash_plan_included: bool


def build_final_pretrade_review(
    config: Settings,
    store: SQLiteStore,
    client,
    trade_date: str,
    max_price_deviation: float = 0.03,
) -> FinalPreTradeReview:
    validations = validate_approved_candidates(
        client,
        store,
        trade_date,
        max_price_deviation=max_price_deviation,
    )
    message = build_pretrade_review_message(trade_date, validations)
    if not validations:
        return FinalPreTradeReview(
            trade_date=trade_date,
            validations=validations,
            message=message,
            cash_plan_included=False,
        )

    balance = sync_overseas_balance(client, store)
    parking_price = None
    if should_fetch_parking_price_for_validations(
        balance.cash,
        validations,
        config.cash.min_cash_buffer,
        config.cash.parking_buy_threshold,
    ):
        parking_symbol = config.cash.parking_etfs[0]
        parking_exchange = config.strategy.exchange_by_symbol.get(parking_symbol, "NAS")
        candles = fetch_daily_candles(client, parking_symbol, parking_exchange)
        parking_price = candles[-1].close if candles else None

    cash_plan = build_cash_plan(
        cash=balance.cash,
        parking_quantity=parking_quantity_for_config(store, config.cash),
        parking_price=parking_price,
        validations=validations,
        config=config.cash,
    )
    return FinalPreTradeReview(
        trade_date=trade_date,
        validations=validations,
        message=f"{message}\n\n{format_cash_plan(cash_plan)}",
        cash_plan_included=True,
    )
