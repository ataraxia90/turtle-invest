from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from turtle_invest.approval_flow import (
    ApprovalCollectResult,
    ApprovalRequestResult,
    collect_approval,
    collect_final_approval,
    request_approval,
)
from turtle_invest.close_report import CloseReportResult, create_close_report
from turtle_invest.config import Settings
from turtle_invest.final_pretrade import FinalPreTradeReview, build_final_pretrade_review
from turtle_invest.market_calendar import default_us_trade_date, is_trading_day
from turtle_invest.orchestrator import ExecutionResult, execute_final_approved_dry_run
from turtle_invest.rollover import RolloverResult, rollover_pending_candidates
from turtle_invest.storage import SQLiteStore, create_store
from turtle_invest.broker.kis import KISClient
from turtle_invest.telegram import TelegramClient


@dataclass(frozen=True)
class PreMarketResult:
    trade_date: str
    approval_request: ApprovalRequestResult
    approval_collect: Optional[ApprovalCollectResult]


@dataclass(frozen=True)
class MarketCloseResult:
    report_date: str
    execution_results: list[ExecutionResult]
    close_report: CloseReportResult


@dataclass(frozen=True)
class FinalPreTradeResult:
    trade_date: str
    review: FinalPreTradeReview
    sent: bool
    final_collect: Optional[ApprovalCollectResult]


@dataclass(frozen=True)
class PostMarketResult:
    report_date: str
    market_close: MarketCloseResult
    rollover: Optional[RolloverResult]


def run_pre_market(
    config: Settings,
    trade_date: Optional[str] = None,
    equity_override: Optional[float] = None,
    collect_timeout: Optional[int] = None,
    force: bool = False,
) -> PreMarketResult:
    run_date = trade_date or default_us_trade_date()
    if not force and not is_trading_day(run_date):
        request = ApprovalRequestResult(
            trade_date=run_date,
            candidates_count=0,
            sent=False,
            message="Skipped: not a trading day.",
        )
        return PreMarketResult(trade_date=run_date, approval_request=request, approval_collect=None)
    request = request_approval(config, trade_date=run_date, equity_override=equity_override)
    collect = None
    if collect_timeout is not None and request.candidates_count > 0:
        collect = collect_approval(config, trade_date=run_date, timeout=collect_timeout)
    return PreMarketResult(trade_date=run_date, approval_request=request, approval_collect=collect)


def run_final_pretrade(
    config: Settings,
    trade_date: Optional[str] = None,
    max_price_deviation: float = 0.03,
    send: bool = False,
    collect_timeout: Optional[int] = None,
    force: bool = False,
) -> FinalPreTradeResult:
    run_date = trade_date or default_us_trade_date()
    if not force and not is_trading_day(run_date):
        review = FinalPreTradeReview(
            trade_date=run_date,
            validations=[],
            message="Skipped: not a trading day.",
            cash_plan_included=False,
        )
        return FinalPreTradeResult(trade_date=run_date, review=review, sent=False, final_collect=None)

    store = create_store(config)
    store.initialize()
    review = build_final_pretrade_review(
        config,
        store,
        KISClient(config.broker),
        run_date,
        max_price_deviation=max_price_deviation,
    )
    sent = False
    if send:
        TelegramClient(config.telegram).send_message(review.message)
        sent = True

    final_collect = None
    if collect_timeout is not None:
        final_collect = collect_final_approval(config, trade_date=run_date, timeout=collect_timeout)

    return FinalPreTradeResult(
        trade_date=run_date,
        review=review,
        sent=sent,
        final_collect=final_collect,
    )


def run_market_close(
    config: Settings,
    report_date: Optional[str] = None,
    send_report: bool = False,
    force: bool = False,
    local_only: bool = False,
) -> MarketCloseResult:
    run_date = report_date or default_us_trade_date()
    if not force and not is_trading_day(run_date):
        close_report = CloseReportResult(
            report_date=run_date,
            report_id=0,
            filled_count=0,
            pending_count=0,
            failed_count=0,
            sent=False,
            message="Skipped: not a trading day.",
        )
        return MarketCloseResult(report_date=run_date, execution_results=[], close_report=close_report)
    store = create_store(config)
    store.initialize()
    execution_results = execute_final_approved_dry_run(store, run_date)
    close_report = create_close_report(
        config,
        report_date=run_date,
        send=send_report,
        local_only=local_only,
    )
    return MarketCloseResult(
        report_date=run_date,
        execution_results=execution_results,
        close_report=close_report,
    )


def run_post_market(
    config: Settings,
    report_date: Optional[str] = None,
    send_report: bool = False,
    force: bool = False,
    local_only: bool = False,
    rollover: bool = True,
    rollover_target_date: Optional[str] = None,
) -> PostMarketResult:
    market_close = run_market_close(
        config,
        report_date=report_date,
        send_report=send_report,
        force=force,
        local_only=local_only,
    )
    if market_close.close_report.report_id == 0:
        return PostMarketResult(
            report_date=market_close.report_date,
            market_close=market_close,
            rollover=None,
        )

    rollover_result = None
    if rollover:
        rollover_result = rollover_pending_candidates(
            create_store(config),
            source_date=market_close.report_date,
            target_date=rollover_target_date,
        )
    return PostMarketResult(
        report_date=market_close.report_date,
        market_close=market_close,
        rollover=rollover_result,
    )
