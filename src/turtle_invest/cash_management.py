from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from turtle_invest.config import CashConfig
from turtle_invest.pretrade import PreTradeValidation
from turtle_invest.storage import SQLiteStore
from turtle_invest.strategy import StrategySignal
from turtle_invest.telegram import STRATEGY_PREFIX


@dataclass(frozen=True)
class CashPlanAction:
    symbol: str
    action: str
    quantity: int
    estimated_price: float
    notional: float
    reason: str


@dataclass(frozen=True)
class CashPlan:
    cash: float
    required_buy_notional: float
    sell_notional: float
    available_after_approved_orders: float
    primary_parking_etf: str
    parking_quantity: int
    parking_price: Optional[float]
    actions: list[CashPlanAction]
    message: str


def build_cash_plan(
    cash: float,
    parking_quantity: int,
    parking_price: Optional[float],
    validations: list[PreTradeValidation],
    config: CashConfig,
) -> CashPlan:
    required_buy_notional = sum(
        validation.notional
        for validation in validations
        if should_include_buy_notional(validation)
    )
    sell_notional = sum(
        validation.notional
        for validation in validations
        if validation.ok and validation.candidate.action == "SELL"
    )
    return build_cash_plan_from_amounts(
        cash=cash,
        parking_quantity=parking_quantity,
        parking_price=parking_price,
        required_buy_notional=required_buy_notional,
        sell_notional=sell_notional,
        config=config,
    )


def build_cash_plan_from_signals(
    cash: float,
    parking_quantity: int,
    parking_price: Optional[float],
    signals: list[StrategySignal],
    config: CashConfig,
) -> CashPlan:
    required_buy_notional = sum(
        signal.quantity * signal.reference_price
        for signal in signals
        if signal.quantity > 0 and signal.action == "BUY"
    )
    sell_notional = sum(
        signal.quantity * signal.reference_price
        for signal in signals
        if signal.quantity > 0 and signal.action == "SELL"
    )
    return build_cash_plan_from_amounts(
        cash=cash,
        parking_quantity=parking_quantity,
        parking_price=parking_price,
        required_buy_notional=required_buy_notional,
        sell_notional=sell_notional,
        config=config,
    )


def build_cash_plan_from_amounts(
    cash: float,
    parking_quantity: int,
    parking_price: Optional[float],
    required_buy_notional: float,
    sell_notional: float,
    config: CashConfig,
) -> CashPlan:
    primary_etf = config.parking_etfs[0]
    available_after_orders = cash + sell_notional - required_buy_notional
    actions: list[CashPlanAction] = []

    if available_after_orders < config.min_cash_buffer:
        shortfall = config.min_cash_buffer - available_after_orders
        if is_positive_price(parking_price) and parking_quantity > 0:
            quantity = min(parking_quantity, int(math.ceil(shortfall / float(parking_price))))
            actions.append(
                CashPlanAction(
                    symbol=primary_etf,
                    action="SELL",
                    quantity=quantity,
                    estimated_price=float(parking_price),
                    notional=quantity * float(parking_price),
                    reason="cash buffer shortfall",
                )
            )
        return CashPlan(
            cash=cash,
            required_buy_notional=required_buy_notional,
            sell_notional=sell_notional,
            available_after_approved_orders=available_after_orders,
            primary_parking_etf=primary_etf,
            parking_quantity=parking_quantity,
            parking_price=parking_price,
            actions=actions,
            message=shortfall_message(shortfall, parking_quantity, parking_price, actions),
        )

    investable_cash = available_after_orders - config.min_cash_buffer
    if config.parking_buy_threshold > 0 and investable_cash >= config.parking_buy_threshold:
        if is_positive_price(parking_price):
            quantity = int(math.floor(investable_cash / float(parking_price)))
            if quantity > 0:
                actions.append(
                    CashPlanAction(
                        symbol=primary_etf,
                        action="BUY",
                        quantity=quantity,
                        estimated_price=float(parking_price),
                        notional=quantity * float(parking_price),
                        reason="surplus cash above threshold",
                    )
                )

    return CashPlan(
        cash=cash,
        required_buy_notional=required_buy_notional,
        sell_notional=sell_notional,
        available_after_approved_orders=available_after_orders,
        primary_parking_etf=primary_etf,
        parking_quantity=parking_quantity,
        parking_price=parking_price,
        actions=actions,
        message=normal_message(actions, config.parking_buy_threshold, investable_cash),
    )


def format_cash_plan(plan: CashPlan) -> str:
    lines = [
        f"{STRATEGY_PREFIX} 현금 관리 계획",
        f"현금: {plan.cash:.2f}",
        f"승인 매수 금액: {plan.required_buy_notional:.2f}",
        f"승인 매도 금액: {plan.sell_notional:.2f}",
        f"승인 주문 후 가용 현금: {plan.available_after_approved_orders:.2f}",
        f"파킹 ETF: {plan.primary_parking_etf}",
        f"파킹 ETF 수량: {plan.parking_quantity}",
        f"파킹 ETF 가격: {format_optional_price(plan.parking_price)}",
        f"메시지: {plan.message}",
    ]
    if not plan.actions:
        lines.append("조치: 없음")
        return "\n".join(lines)

    lines.append("조치:")
    for action in plan.actions:
        lines.append(
            f"- {action.action} {action.symbol} 수량={action.quantity} "
            f"가격={action.estimated_price:.2f} 금액={action.notional:.2f} 사유={action.reason}"
        )
    return "\n".join(lines)


def parking_quantity_for_config(store: SQLiteStore, config: CashConfig) -> int:
    for symbol in config.parking_etfs:
        row = store.get_position(symbol)
        if row is not None:
            return int(row["quantity"])
    return 0


def should_fetch_parking_price_for_amounts(
    cash: float,
    required_buy_notional: float,
    sell_notional: float,
    min_cash_buffer: float,
    parking_buy_threshold: float,
) -> bool:
    available_after_orders = cash + sell_notional - required_buy_notional
    if available_after_orders < min_cash_buffer:
        return True
    investable_cash = available_after_orders - min_cash_buffer
    return parking_buy_threshold > 0 and investable_cash >= parking_buy_threshold


def should_fetch_parking_price_for_signals(
    cash: float,
    signals: list[StrategySignal],
    min_cash_buffer: float,
    parking_buy_threshold: float,
) -> bool:
    required_buy_notional = sum(
        signal.quantity * signal.reference_price
        for signal in signals
        if signal.quantity > 0 and signal.action == "BUY"
    )
    sell_notional = sum(
        signal.quantity * signal.reference_price
        for signal in signals
        if signal.quantity > 0 and signal.action == "SELL"
    )
    return should_fetch_parking_price_for_amounts(
        cash,
        required_buy_notional,
        sell_notional,
        min_cash_buffer,
        parking_buy_threshold,
    )


def should_fetch_parking_price_for_validations(
    cash: float,
    validations: list[PreTradeValidation],
    min_cash_buffer: float,
    parking_buy_threshold: float,
) -> bool:
    required_buy_notional = sum(
        validation.notional for validation in validations if should_include_buy_notional(validation)
    )
    sell_notional = sum(
        validation.notional
        for validation in validations
        if validation.ok and validation.candidate.action == "SELL"
    )
    return should_fetch_parking_price_for_amounts(
        cash,
        required_buy_notional,
        sell_notional,
        min_cash_buffer,
        parking_buy_threshold,
    )


def is_positive_price(value: Optional[float]) -> bool:
    return value is not None and value > 0


def should_include_buy_notional(validation: PreTradeValidation) -> bool:
    if validation.candidate.action != "BUY":
        return False
    return validation.ok or validation.message == "insufficient cash"


def shortfall_message(
    shortfall: float,
    parking_quantity: int,
    parking_price: Optional[float],
    actions: list[CashPlanAction],
) -> str:
    if actions:
        return f"파킹 ETF 매도로 현금 부족분 {shortfall:.2f}을 충당합니다"
    if not is_positive_price(parking_price):
        return f"현금 부족분 {shortfall:.2f}; 파킹 ETF 가격을 확인할 수 없습니다"
    if parking_quantity <= 0:
        return f"현금 부족분 {shortfall:.2f}; 보유 중인 파킹 ETF가 없습니다"
    return f"현금 부족분 {shortfall:.2f}"


def normal_message(actions: list[CashPlanAction], threshold: float, investable_cash: float) -> str:
    if actions:
        return "초과 현금으로 파킹 ETF를 매수합니다"
    if threshold <= 0:
        return "파킹 ETF 매수 기준금액이 설정되지 않았습니다"
    if investable_cash < threshold:
        return "초과 현금이 파킹 ETF 매수 기준금액보다 작습니다"
    return "필요한 파킹 ETF 조치가 없습니다"


def format_optional_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"
