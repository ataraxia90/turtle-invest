from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from turtle_invest.config import Settings
from turtle_invest.market_calendar import default_us_trade_date
from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.orchestrator import DailyOrchestrator, make_idempotency_key
from turtle_invest.risk_controls import RiskControlContext, apply_portfolio_risk_limits
from turtle_invest.storage import SQLiteStore, StoredOrderCandidate
from turtle_invest.strategy import Position, SignalAction, StrategySignal, evaluate_symbol
from turtle_invest.telegram import STRATEGY_PREFIX, build_approval_message
from turtle_invest.universe import active_universe


PAPER_ACCOUNT_KEY = "paper_account:default"


@dataclass(frozen=True)
class PaperPosition:
    symbol: str
    quantity: int
    units: int
    average_price: float
    last_entry_price: Optional[float] = None


@dataclass(frozen=True)
class PaperAccount:
    cash: float
    initial_cash: float
    positions: dict[str, PaperPosition]
    updated_at: str


@dataclass(frozen=True)
class PaperStatus:
    cash: float
    positions_value: float
    total_equity: float
    positions: list[PaperPosition]


@dataclass(frozen=True)
class PaperPlanResult:
    trade_date: str
    cash: float
    total_equity: float
    signals: list[StrategySignal]
    saved_candidates: int
    approval_message: str


@dataclass(frozen=True)
class PaperExecution:
    symbol: str
    action: str
    quantity: int
    price: float
    notional: float
    status: str
    message: str


@dataclass(frozen=True)
class PaperExecutionResult:
    trade_date: str
    executions: list[PaperExecution]
    cash: float
    total_equity: float


@dataclass(frozen=True)
class PaperRunResult:
    trade_date: str
    plan: PaperPlanResult
    execution: PaperExecutionResult
    report_message: str
    sent: bool = False


def initialize_paper_account(store: SQLiteStore, cash: float = 10000.0, reset: bool = False) -> PaperAccount:
    store.initialize()
    existing = store.get_state(PAPER_ACCOUNT_KEY)
    if existing and not reset:
        return load_paper_account(store)
    account = PaperAccount(
        cash=cash,
        initial_cash=cash,
        positions={},
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    save_paper_account(store, account)
    return account


def load_paper_account(store: SQLiteStore) -> PaperAccount:
    store.initialize()
    raw = store.get_state(PAPER_ACCOUNT_KEY)
    if not raw:
        return initialize_paper_account(store)
    data = json.loads(raw)
    positions = {
        symbol: PaperPosition(
            symbol=symbol,
            quantity=int(value["quantity"]),
            units=int(value["units"]),
            average_price=float(value["average_price"]),
            last_entry_price=value.get("last_entry_price"),
        )
        for symbol, value in data.get("positions", {}).items()
    }
    return PaperAccount(
        cash=float(data["cash"]),
        initial_cash=float(data.get("initial_cash", data["cash"])),
        positions=positions,
        updated_at=str(data.get("updated_at", "")),
    )


def save_paper_account(store: SQLiteStore, account: PaperAccount) -> None:
    payload = {
        "cash": account.cash,
        "initial_cash": account.initial_cash,
        "updated_at": account.updated_at,
        "positions": {symbol: asdict(position) for symbol, position in account.positions.items()},
    }
    store.set_state(PAPER_ACCOUNT_KEY, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def get_paper_status(config: Settings, store: SQLiteStore) -> PaperStatus:
    account = load_paper_account(store)
    prices = fetch_latest_prices_for_positions(config, account)
    positions_value = sum(
        position.quantity * prices.get(symbol, position.average_price)
        for symbol, position in account.positions.items()
    )
    return PaperStatus(
        cash=account.cash,
        positions_value=positions_value,
        total_equity=account.cash + positions_value,
        positions=sorted(account.positions.values(), key=lambda position: position.symbol),
    )


def create_paper_daily_plan(
    config: Settings,
    store: SQLiteStore,
    trade_date: Optional[str] = None,
) -> PaperPlanResult:
    account = load_paper_account(store)
    run_date = trade_date or default_us_trade_date()
    universe = active_universe(config, store)
    candles_by_symbol = {}
    latest_prices = {}
    for member in universe:
        candles = fetch_daily_candles_for_symbol(config, member.symbol, member.exchange)
        candles_by_symbol[member.symbol] = candles
        if candles:
            latest_prices[member.symbol] = candles[-1].close

    total_equity = paper_total_equity(account, latest_prices)
    signals = []
    for member in universe:
        paper_position = account.positions.get(member.symbol)
        position = Position(
            symbol=member.symbol,
            quantity=paper_position.quantity if paper_position else 0,
            units=paper_position.units if paper_position else 0,
            last_entry_price=paper_position.last_entry_price if paper_position else None,
        )
        signals.append(
            evaluate_symbol(
                symbol=member.symbol,
                candles=candles_by_symbol[member.symbol],
                position=position,
                total_equity=total_equity,
                config=config.strategy,
            )
        )
    signals = apply_portfolio_risk_limits(
        signals,
        RiskControlContext(
            total_equity=total_equity,
            positions={
                symbol: Position(
                    symbol=symbol,
                    quantity=position.quantity,
                    units=position.units,
                    last_entry_price=position.last_entry_price,
                )
                for symbol, position in account.positions.items()
            },
            latest_prices=latest_prices,
        ),
        config.strategy,
    )

    saved = record_paper_candidates(store, run_date, signals)
    return PaperPlanResult(
        trade_date=run_date,
        cash=account.cash,
        total_equity=total_equity,
        signals=signals,
        saved_candidates=len(saved),
        approval_message=build_approval_message(run_date, signals),
    )


def execute_paper_candidates(
    config: Settings,
    store: SQLiteStore,
    trade_date: Optional[str] = None,
) -> PaperExecutionResult:
    run_date = trade_date or default_us_trade_date()
    account = load_paper_account(store)
    candidates = [
        candidate
        for candidate in store.list_order_candidates(run_date)
        if candidate.payload.get("paper") is True and not store.has_order_event(candidate.idempotency_key)
    ]
    executions: list[PaperExecution] = []
    for candidate in candidates:
        price = paper_fill_price(config, candidate, run_date)
        execution, account = apply_paper_execution(account, candidate, price)
        store.record_order_event(
            order_candidate_id=candidate.id,
            broker_order_id=None,
            status=execution.status,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            payload={
                "paper": True,
                "symbol": candidate.symbol,
                "action": candidate.action,
                "quantity": candidate.quantity,
                "price": price,
                "message": execution.message,
            },
            idempotency_key=candidate.idempotency_key,
        )
        executions.append(execution)
    save_paper_account(store, account)
    status = get_paper_status(config, store)
    return PaperExecutionResult(
        trade_date=run_date,
        executions=executions,
        cash=status.cash,
        total_equity=status.total_equity,
    )


def run_paper_day(
    config: Settings,
    store: SQLiteStore,
    trade_date: Optional[str] = None,
    send_report: bool = False,
) -> PaperRunResult:
    run_date = trade_date or default_us_trade_date()
    plan = create_paper_daily_plan(config, store, trade_date=run_date)
    execution = execute_paper_candidates(config, store, trade_date=run_date)
    message = build_paper_report_message(plan, execution)
    sent = False
    if send_report:
        from turtle_invest.telegram import TelegramClient

        TelegramClient(config.telegram).send_message(message)
        sent = True
    return PaperRunResult(
        trade_date=run_date,
        plan=plan,
        execution=execution,
        report_message=message,
        sent=sent,
    )


def build_paper_report_message(plan: PaperPlanResult, execution: PaperExecutionResult) -> str:
    actionable = [signal for signal in plan.signals if signal.quantity > 0]
    filled = [item for item in execution.executions if item.status == "PAPER_FILLED"]
    blocked = [item for item in execution.executions if item.status != "PAPER_FILLED"]
    lines = [
        f"{STRATEGY_PREFIX}[{plan.trade_date}] 모의투자 사후보고",
        f"시작 현금: {plan.cash:.2f}",
        f"시작 평가자산: {plan.total_equity:.2f}",
        f"신호: {len(plan.signals)}",
        f"실행 대상 신호: {len(actionable)}",
        f"모의 체결 시도: {len(execution.executions)}",
        f"체결: {len(filled)}",
        f"차단: {len(blocked)}",
        f"종료 현금: {execution.cash:.2f}",
        f"종료 평가자산: {execution.total_equity:.2f}",
    ]
    if execution.executions:
        lines.append("")
        lines.append("모의 체결 내역:")
        for item in execution.executions:
            lines.append(
                f"- {item.status} {item.symbol} {item.action} "
                f"수량={item.quantity} 가격={item.price:.2f} 금액={item.notional:.2f}"
            )
    else:
        lines.append("")
        lines.append("모의 체결 없음.")
    return "\n".join(lines)


def record_paper_candidates(
    store: SQLiteStore,
    trade_date: str,
    signals: list[StrategySignal],
) -> list[StoredOrderCandidate]:
    saved = []
    for signal in signals:
        candidate = paper_signal_to_candidate(trade_date, signal)
        if candidate is not None and store.record_order_candidate(candidate):
            saved.append(candidate)
    return saved


def paper_signal_to_candidate(trade_date: str, signal: StrategySignal) -> Optional[StoredOrderCandidate]:
    if signal.action == SignalAction.HOLD or signal.quantity <= 0:
        return None
    payload = asdict(signal)
    payload["paper"] = True
    return StoredOrderCandidate(
        trade_date=trade_date,
        symbol=signal.symbol,
        action=signal.action.value,
        quantity=signal.quantity,
        reason=f"PAPER_{signal.reason.value}",
        idempotency_key=f"{trade_date}:PAPER:{make_idempotency_key(trade_date, signal)}",
        payload=payload,
    )


def apply_paper_execution(
    account: PaperAccount,
    candidate: StoredOrderCandidate,
    price: float,
) -> tuple[PaperExecution, PaperAccount]:
    notional = candidate.quantity * price
    positions = dict(account.positions)
    current = positions.get(candidate.symbol)
    if candidate.action == "BUY":
        if account.cash < notional:
            return (
                PaperExecution(candidate.symbol, candidate.action, candidate.quantity, price, notional, "PAPER_BLOCKED", "모의 현금 부족"),
                account,
            )
        existing_quantity = current.quantity if current else 0
        existing_cost = existing_quantity * current.average_price if current else 0.0
        new_quantity = existing_quantity + candidate.quantity
        positions[candidate.symbol] = PaperPosition(
            symbol=candidate.symbol,
            quantity=new_quantity,
            units=int(candidate.payload.get("units_after") or (current.units + 1 if current else 1)),
            average_price=(existing_cost + notional) / new_quantity,
            last_entry_price=price,
        )
        return (
            PaperExecution(candidate.symbol, candidate.action, candidate.quantity, price, notional, "PAPER_FILLED", "모의 매수 시장가 체결"),
            replace_account(account, cash=account.cash - notional, positions=positions),
        )

    if candidate.action == "SELL":
        held = current.quantity if current else 0
        if held < candidate.quantity:
            return (
                PaperExecution(candidate.symbol, candidate.action, candidate.quantity, price, notional, "PAPER_BLOCKED", "모의 보유수량 부족"),
                account,
            )
        remaining = held - candidate.quantity
        if remaining > 0 and current is not None:
            positions[candidate.symbol] = PaperPosition(
                symbol=candidate.symbol,
                quantity=remaining,
                units=0 if remaining == 0 else max(1, current.units - 1),
                average_price=current.average_price,
                last_entry_price=current.last_entry_price,
            )
        else:
            positions.pop(candidate.symbol, None)
        return (
            PaperExecution(candidate.symbol, candidate.action, candidate.quantity, price, notional, "PAPER_FILLED", "모의 매도 시장가 체결"),
            replace_account(account, cash=account.cash + notional, positions=positions),
        )

    return (
        PaperExecution(candidate.symbol, candidate.action, candidate.quantity, price, notional, "PAPER_BLOCKED", "지원하지 않는 모의 주문 유형"),
        account,
    )


def paper_fill_price(config: Settings, candidate: StoredOrderCandidate, trade_date: str) -> float:
    exchange = config.strategy.exchange_by_symbol.get(candidate.symbol, "NAS")
    candles = fetch_daily_candles_for_symbol(config, candidate.symbol, exchange)
    if not candles:
        return float(candidate.payload.get("reference_price") or 0)
    latest = candles[-1]
    return latest.open if normalize_date(latest.date) == trade_date else latest.close


def fetch_daily_candles_for_symbol(config: Settings, symbol: str, exchange: str):
    from turtle_invest.broker.kis import KISClient

    return fetch_daily_candles(KISClient(config.broker), symbol, exchange)


def fetch_latest_prices_for_positions(config: Settings, account: PaperAccount) -> dict[str, float]:
    prices = {}
    for symbol in account.positions:
        exchange = config.strategy.exchange_by_symbol.get(symbol, "NAS")
        candles = fetch_daily_candles_for_symbol(config, symbol, exchange)
        if candles:
            prices[symbol] = candles[-1].close
    return prices


def paper_total_equity(account: PaperAccount, latest_prices: dict[str, float]) -> float:
    positions_value = sum(
        position.quantity * latest_prices.get(symbol, position.average_price)
        for symbol, position in account.positions.items()
    )
    return account.cash + positions_value


def replace_account(
    account: PaperAccount,
    cash: float,
    positions: dict[str, PaperPosition],
) -> PaperAccount:
    return PaperAccount(
        cash=cash,
        initial_cash=account.initial_cash,
        positions=positions,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def normalize_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value
