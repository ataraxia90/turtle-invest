from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from turtle_invest.config import ConfigError, load_config
from turtle_invest.approval_flow import collect_approval, collect_final_approval, request_approval
from turtle_invest.backup import backup_file
from turtle_invest.backtest import fetch_universe_candles, run_backtest, save_backtest_result
from turtle_invest.cash_management import (
    build_cash_plan,
    format_cash_plan,
    parking_quantity_for_config,
    should_fetch_parking_price_for_amounts,
    should_include_buy_notional,
)
from turtle_invest.command_help import format_commands
from turtle_invest.close_report import create_close_report
from turtle_invest.daily_plan import create_daily_plan
from turtle_invest.final_pretrade import build_final_pretrade_review
from turtle_invest.logging_config import configure_logging
from turtle_invest.broker.kis import KISClient, KISClientError
from turtle_invest.market_calendar import default_us_trade_date, is_after_regular_market_open, is_trading_day, next_trading_day
from turtle_invest.market_data import fetch_daily_candles
from turtle_invest.order_execution import (
    LIVE_ORDER_CONFIRMATION,
    OrderExecutionError,
    execute_final_approved_live_orders,
)
from turtle_invest.orchestrator import execute_approved_dry_run, execute_candidates_dry_run, execute_final_approved_dry_run
from turtle_invest.operations import run_final_pretrade, run_market_close, run_post_market, run_pre_market
from turtle_invest.paper_trading import (
    create_paper_daily_plan,
    execute_paper_candidates,
    get_paper_status,
    initialize_paper_account,
    run_paper_day,
)
from turtle_invest.portfolio_sync import sync_overseas_balance
from turtle_invest.pretrade import validate_approved_candidates
from turtle_invest.rehearsal import run_local_rehearsal
from turtle_invest.rollover import rollover_pending_candidates
from turtle_invest.safety import check_safety
from turtle_invest.status import get_runtime_status
from turtle_invest.storage import SQLiteStore
from turtle_invest.strategy import Position, evaluate_symbol
from turtle_invest.tax_harvest import build_tax_harvest_message, build_tax_harvest_report, parse_price_overrides
from turtle_invest.telegram import TelegramClient, TelegramClientError
from turtle_invest.universe import active_universe, refresh_universe_from_config


def build_parser() -> argparse.ArgumentParser:
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument(
        "--config",
        default="config.local.json",
        help="Path to a JSON config file. Defaults to config.local.json.",
    )

    parser = argparse.ArgumentParser(
        prog="turtle-invest",
        description="US turtle strategy portfolio automation.",
        parents=[config_parent],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "doctor",
        parents=[config_parent],
        help="Check local runtime and config shape.",
    )
    subparsers.add_parser(
        "show-config",
        parents=[config_parent],
        help="Print non-secret config values.",
    )
    subparsers.add_parser(
        "init-db",
        parents=[config_parent],
        help="Initialize the local SQLite database.",
    )
    subparsers.add_parser(
        "sync-balance",
        parents=[config_parent],
        help="Read overseas stock balance and store a local snapshot. Read-only.",
    )
    fetch_daily = subparsers.add_parser(
        "fetch-daily",
        parents=[config_parent],
        help="Read overseas daily candles for a symbol. Read-only.",
    )
    fetch_daily.add_argument("symbol", help="US stock symbol, e.g. AAPL.")
    fetch_daily.add_argument("--exchange", default="NAS", help="KIS exchange code. Defaults to NAS.")

    analyze_symbol = subparsers.add_parser(
        "analyze-symbol",
        parents=[config_parent],
        help="Read daily candles and calculate the strategy signal. Read-only.",
    )
    analyze_symbol.add_argument("symbol", help="US stock symbol, e.g. AAPL.")
    analyze_symbol.add_argument("--exchange", default="NAS", help="KIS exchange code. Defaults to NAS.")
    analyze_symbol.add_argument("--equity", type=float, default=0.0, help="Total equity for unit sizing.")

    analyze_universe = subparsers.add_parser(
        "analyze-universe",
        parents=[config_parent],
        help="Read daily candles and calculate strategy signals for configured symbols. Read-only.",
    )
    analyze_universe.add_argument("--exchange", default="NAS", help="KIS exchange code. Defaults to NAS.")
    analyze_universe.add_argument("--equity", type=float, default=0.0, help="Total equity for unit sizing.")

    backtest_parser = subparsers.add_parser(
        "backtest",
        parents=[config_parent],
        help="Run a recent-history backtest using KIS daily candles.",
    )
    backtest_parser.add_argument("--equity", type=float, required=True, help="Initial equity.")
    backtest_parser.add_argument("--output", default=None, help="Optional JSON output path.")

    refresh_universe_parser = subparsers.add_parser(
        "refresh-universe",
        parents=[config_parent],
        help="Store the current configured universe as the active universe snapshot.",
    )
    refresh_universe_parser.add_argument("--date", default=None, help="Universe date. Defaults to US date.")
    refresh_universe_parser.add_argument("--source", default="config", help="Source label stored with the snapshot.")

    show_universe_parser = subparsers.add_parser(
        "show-universe",
        parents=[config_parent],
        help="Show the active stored universe, or config fallback if none is stored.",
    )
    show_universe_parser.add_argument("--date", default=None, help="Stored universe date to show.")

    plan_day = subparsers.add_parser(
        "plan-day",
        parents=[config_parent],
        help="Sync balance, analyze universe, and store order candidates. Read-only except local DB writes.",
    )
    plan_day.add_argument("--trade-date", default=None, help="Trade date for idempotency keys. Defaults to today.")
    plan_day.add_argument("--equity", type=float, default=None, help="Override total equity for unit sizing.")

    paper_init = subparsers.add_parser(
        "paper-init",
        parents=[config_parent],
        help="Initialize local paper account cash. No broker orders.",
    )
    paper_init.add_argument("--cash", type=float, default=10000.0, help="Initial paper cash.")
    paper_init.add_argument("--reset", action="store_true", help="Reset existing paper account.")

    subparsers.add_parser(
        "paper-status",
        parents=[config_parent],
        help="Show local paper account cash, positions, and marked equity.",
    )

    paper_plan = subparsers.add_parser(
        "paper-plan-day",
        parents=[config_parent],
        help="Use KIS prices and local paper account to create paper order candidates.",
    )
    paper_plan.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")

    paper_execute = subparsers.add_parser(
        "paper-execute",
        parents=[config_parent],
        help="Fill paper candidates at market-price assumption and update local paper portfolio.",
    )
    paper_execute.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")

    paper_run = subparsers.add_parser(
        "paper-run-day",
        parents=[config_parent],
        help="Run paper plan and paper execution, then optionally send a Telegram report.",
    )
    paper_run.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    paper_run.add_argument("--send-report", action="store_true", help="Send paper result report to Telegram.")
    paper_run.add_argument("--force", action="store_true", help="Run even if calendar says closed.")
    paper_run.add_argument(
        "--after-open-only",
        action="store_true",
        help="Skip until the US regular market has opened.",
    )
    paper_run.add_argument(
        "--once-per-day",
        action="store_true",
        help="Skip if this paper workflow already completed for the trade date.",
    )

    request_approval_parser = subparsers.add_parser(
        "request-approval",
        parents=[config_parent],
        help="Create today's plan and send Telegram approval request if candidates exist.",
    )
    request_approval_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    request_approval_parser.add_argument("--equity", type=float, default=None, help="Override total equity.")

    collect_approval_parser = subparsers.add_parser(
        "collect-approval",
        parents=[config_parent],
        help="Read Telegram approval responses and store approval state.",
    )
    collect_approval_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    collect_approval_parser.add_argument("--timeout", type=int, default=0, help="Telegram long-poll timeout seconds.")

    collect_final_parser = subparsers.add_parser(
        "collect-final-approval",
        parents=[config_parent],
        help="Read Telegram final execution approval responses.",
    )
    collect_final_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    collect_final_parser.add_argument("--timeout", type=int, default=0, help="Telegram long-poll timeout seconds.")

    close_report_parser = subparsers.add_parser(
        "close-report",
        parents=[config_parent],
        help="Read execution state and create market-close report. Read-only except local DB writes.",
    )
    close_report_parser.add_argument("--report-date", default=None, help="Report date. Defaults to US date.")
    close_report_parser.add_argument("--send", action="store_true", help="Send report to Telegram.")
    close_report_parser.add_argument("--local-only", action="store_true", help="Skip broker queries.")

    execute_parser = subparsers.add_parser(
        "execute-approved",
        parents=[config_parent],
        help="Execute approved candidates in DRY_RUN mode only.",
    )
    execute_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    execute_parser.add_argument("--validate", action="store_true", help="Run pre-trade validation before dry-run.")

    execute_final_parser = subparsers.add_parser(
        "execute-final-approved",
        parents=[config_parent],
        help="Execute final-approved candidates in DRY_RUN mode only.",
    )
    execute_final_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")

    execute_live_parser = subparsers.add_parser(
        "execute-live-approved",
        parents=[config_parent],
        help="Submit final-approved candidates as live KIS orders. Requires explicit confirmation.",
    )
    execute_live_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    execute_live_parser.add_argument(
        "--confirm",
        default="",
        help=f"Required confirmation text: {LIVE_ORDER_CONFIRMATION}",
    )
    execute_live_parser.add_argument("--max-price-deviation", type=float, default=0.03, help="Allowed price drift.")

    validate_parser = subparsers.add_parser(
        "validate-approved",
        parents=[config_parent],
        help="Re-check cash, holdings, and latest prices for approved candidates. Read-only.",
    )
    validate_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    validate_parser.add_argument("--max-price-deviation", type=float, default=0.03, help="Allowed price drift.")
    validate_parser.add_argument("--send", action="store_true", help="Send final pre-trade review to Telegram.")

    final_pretrade_parser = subparsers.add_parser(
        "final-pretrade",
        parents=[config_parent],
        help="Build/send final pre-trade review and optionally collect final approval.",
    )
    final_pretrade_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    final_pretrade_parser.add_argument("--max-price-deviation", type=float, default=0.03, help="Allowed price drift.")
    final_pretrade_parser.add_argument("--send", action="store_true", help="Send final pre-trade review to Telegram.")
    final_pretrade_parser.add_argument(
        "--collect-timeout",
        type=int,
        default=None,
        help="If set, long-poll Telegram for final approval after sending/reviewing.",
    )
    final_pretrade_parser.add_argument("--force", action="store_true", help="Run even if calendar says closed.")

    cash_plan_parser = subparsers.add_parser(
        "cash-plan",
        parents=[config_parent],
        help="Plan parking ETF cash actions around approved orders. Read-only.",
    )
    cash_plan_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    cash_plan_parser.add_argument("--cash", type=float, default=None, help="Override cash amount.")
    cash_plan_parser.add_argument("--parking-price", type=float, default=None, help="Override parking ETF price.")

    tax_parser = subparsers.add_parser(
        "tax-harvest-report",
        parents=[config_parent],
        help="Estimate annual overseas-stock gain harvesting room. Report only; no orders.",
    )
    tax_parser.add_argument("--year", type=int, default=None, help="Tax year. Defaults to US trade date year.")
    tax_parser.add_argument("--usd-krw", type=float, default=None, help="KRW/USD rate for estimate.")
    tax_parser.add_argument(
        "--price",
        action="append",
        default=[],
        help="Override latest price as SYMBOL=PRICE. May be repeated.",
    )
    tax_parser.add_argument("--send", action="store_true", help="Send report to Telegram.")

    pre_market_parser = subparsers.add_parser(
        "pre-market",
        parents=[config_parent],
        help="Run pre-market workflow: plan, request approval, optionally collect response.",
    )
    pre_market_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")
    pre_market_parser.add_argument("--equity", type=float, default=None, help="Override total equity.")
    pre_market_parser.add_argument(
        "--collect-timeout",
        type=int,
        default=None,
        help="If set, long-poll Telegram for this many seconds after sending approval.",
    )
    pre_market_parser.add_argument("--force", action="store_true", help="Run even if calendar says closed.")

    market_close_parser = subparsers.add_parser(
        "market-close",
        parents=[config_parent],
        help="Run market-close workflow: dry-run approved candidates and create close report.",
    )
    market_close_parser.add_argument("--report-date", default=None, help="Report date. Defaults to US date.")
    market_close_parser.add_argument("--send-report", action="store_true", help="Send close report to Telegram.")
    market_close_parser.add_argument("--force", action="store_true", help="Run even if calendar says closed.")
    market_close_parser.add_argument("--local-only", action="store_true", help="Skip broker queries.")

    post_market_parser = subparsers.add_parser(
        "post-market",
        parents=[config_parent],
        help="Run market-close report and roll pending candidates to the next trading day.",
    )
    post_market_parser.add_argument("--report-date", default=None, help="Report date. Defaults to US date.")
    post_market_parser.add_argument("--send-report", action="store_true", help="Send close report to Telegram.")
    post_market_parser.add_argument("--force", action="store_true", help="Run even if calendar says closed.")
    post_market_parser.add_argument("--local-only", action="store_true", help="Skip broker queries.")
    post_market_parser.add_argument("--no-rollover", action="store_true", help="Skip pending candidate rollover.")
    post_market_parser.add_argument("--rollover-target-date", default=None, help="Override rollover target date.")

    calendar_parser = subparsers.add_parser(
        "calendar",
        parents=[config_parent],
        help="Show US trading-day status for a date.",
    )
    calendar_parser.add_argument("--date", default=None, help="Date in YYYY-MM-DD. Defaults to US date.")

    subparsers.add_parser(
        "safety-check",
        parents=[config_parent],
        help="Show whether live order execution is locked or enabled.",
    )

    status_parser = subparsers.add_parser(
        "status",
        parents=[config_parent],
        help="Show runtime status, calendar, safety, and local DB counts.",
    )
    status_parser.add_argument("--date", default=None, help="Date in YYYY-MM-DD. Defaults to US date.")

    rehearsal_parser = subparsers.add_parser(
        "rehearse-local",
        parents=[config_parent],
        help="Run a local-only approval and dry-run execution rehearsal. No network calls.",
    )
    rehearsal_parser.add_argument("--trade-date", default="2099-01-01", help="Synthetic rehearsal trade date.")

    orders_parser = subparsers.add_parser(
        "orders",
        parents=[config_parent],
        help="List local order candidates, approvals, and execution status.",
    )
    orders_parser.add_argument("--trade-date", default=None, help="Trade date. Defaults to US date.")

    rollover_parser = subparsers.add_parser(
        "rollover-pending",
        parents=[config_parent],
        help="Copy unexecuted or failed candidates to the next trading day for re-approval.",
    )
    rollover_parser.add_argument("--source-date", default=None, help="Source trade date. Defaults to US date.")
    rollover_parser.add_argument("--target-date", default=None, help="Target trade date. Defaults to next trading day.")

    backup_parser = subparsers.add_parser(
        "backup-db",
        parents=[config_parent],
        help="Copy the local SQLite database to data/backups.",
    )
    backup_parser.add_argument("--backup-dir", default="data/backups", help="Backup output directory.")

    subparsers.add_parser(
        "commands",
        parents=[config_parent],
        help="Show common operational commands.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.app.log_level)

    if args.command == "doctor":
        print("turtle-invest runtime OK")
        print(f"env={config.app.env}")
        print(f"broker={config.broker.provider}:{config.broker.mode}")
        print(f"timezone={config.app.timezone}")
        return 0

    if args.command == "show-config":
        print(json.dumps(config.to_safe_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "init-db":
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        print(f"database initialized: {config.app.database_path}")
        return 0

    if args.command == "sync-balance":
        try:
            result = sync_overseas_balance(KISClient(config.broker), SQLiteStore(config.app.database_path))
        except KISClientError as exc:
            print(f"KIS error: {exc}", file=sys.stderr)
            return 1
        print("balance synced")
        print(f"captured_at={result.captured_at}")
        print(f"snapshot_id={result.snapshot_id}")
        print(f"positions_count={result.positions_count}")
        print(f"total_equity={result.total_equity}")
        print(f"cash={result.cash}")
        print(f"message={result.message.strip()}")
        return 0

    if args.command == "fetch-daily":
        try:
            candles = fetch_daily_candles(KISClient(config.broker), args.symbol, args.exchange)
        except KISClientError as exc:
            print(f"KIS error: {exc}", file=sys.stderr)
            return 1
        print(f"symbol={args.symbol.upper()}")
        print(f"candles={len(candles)}")
        if candles:
            latest = candles[-1]
            print(f"latest={latest.date} close={latest.close}")
        return 0

    if args.command == "analyze-symbol":
        try:
            candles = fetch_daily_candles(KISClient(config.broker), args.symbol, args.exchange)
        except KISClientError as exc:
            print(f"KIS error: {exc}", file=sys.stderr)
            return 1
        signal = evaluate_symbol(
            symbol=args.symbol.upper(),
            candles=candles,
            position=Position(args.symbol.upper(), quantity=0, units=0),
            total_equity=args.equity,
            config=config.strategy,
        )
        print(f"symbol={signal.symbol}")
        print(f"action={signal.action.value}")
        print(f"reason={signal.reason.value}")
        print(f"quantity={signal.quantity}")
        print(f"reference_price={signal.reference_price}")
        print(f"atr={signal.atr}")
        print(f"threshold={signal.threshold}")
        print(f"message={signal.message}")
        return 0

    if args.command == "analyze-universe":
        client = KISClient(config.broker)
        print(f"symbols={len(config.strategy.symbols)}")
        for symbol in config.strategy.symbols[: config.strategy.universe_size]:
            exchange = config.strategy.exchange_by_symbol[symbol]
            try:
                candles = fetch_daily_candles(client, symbol, exchange)
            except KISClientError as exc:
                print(f"{symbol}: ERROR {exc}")
                continue
            signal = evaluate_symbol(
                symbol=symbol,
                candles=candles,
                position=Position(symbol, quantity=0, units=0),
                total_equity=args.equity,
                config=config.strategy,
            )
            print(
                f"{symbol}: action={signal.action.value} reason={signal.reason.value} "
                f"qty={signal.quantity} atr={signal.atr} ref={signal.reference_price}"
            )
        return 0

    if args.command == "backtest":
        try:
            candles_by_symbol = fetch_universe_candles(KISClient(config.broker), config)
            result = run_backtest(candles_by_symbol, config.strategy, args.equity)
        except (KISClientError, ValueError) as exc:
            print(f"Backtest error: {exc}", file=sys.stderr)
            return 1
        print(f"start_date={result.start_date}")
        print(f"end_date={result.end_date}")
        print(f"initial_equity={result.initial_equity:.2f}")
        print(f"final_equity={result.final_equity:.2f}")
        print(f"total_return={result.total_return:.4%}")
        print(f"max_drawdown={result.max_drawdown:.4%}")
        print(f"trades={len(result.trades)}")
        open_positions = [position for position in result.positions.values() if position.quantity > 0]
        print(f"open_positions={len(open_positions)}")
        for trade in result.trades[-20:]:
            print(
                f"{trade.date} {trade.symbol} {trade.action} qty={trade.quantity} "
                f"price={trade.price:.2f} reason={trade.reason}"
            )
        if args.output:
            save_backtest_result(result, args.output)
            print(f"output={args.output}")
        return 0

    if args.command == "refresh-universe":
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        result = refresh_universe_from_config(
            config=config,
            store=store,
            universe_date=args.date,
            source=args.source,
        )
        print(f"universe_date={result.universe_date}")
        print(f"source={result.source}")
        print(f"saved_count={result.saved_count}")
        for member in result.members:
            print(f"{member.rank}: {member.symbol} exchange={member.exchange} source={member.source}")
        return 0

    if args.command == "show-universe":
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        if args.date:
            members = store.list_universe_members(args.date)
            print(f"universe_date={args.date}")
            print("source=stored")
            for member in members:
                exchange = config.strategy.exchange_by_symbol.get(member.symbol, "NAS")
                print(f"{member.rank}: {member.symbol} exchange={exchange} source={member.source}")
        else:
            members = active_universe(config, store)
            latest_date = store.latest_universe_date()
            print(f"universe_date={latest_date or 'config-fallback'}")
            print(f"source={'stored' if latest_date else 'config'}")
            for member in members:
                print(f"{member.rank}: {member.symbol} exchange={member.exchange} source={member.source}")
        return 0

    if args.command == "plan-day":
        try:
            result = create_daily_plan(config, trade_date=args.trade_date, equity_override=args.equity)
        except KISClientError as exc:
            print(f"KIS error: {exc}", file=sys.stderr)
            return 1
        actionable = [signal for signal in result.signals if signal.quantity > 0]
        print(f"trade_date={result.trade_date}")
        print(f"synced_positions={result.synced_positions}")
        print(f"total_equity={result.total_equity}")
        print(f"signals={len(result.signals)}")
        print(f"actionable_signals={len(actionable)}")
        print(f"saved_candidates={result.saved_candidates}")
        print("approval_message_start")
        print(result.approval_message)
        print("approval_message_end")
        return 0

    if args.command == "paper-init":
        account = initialize_paper_account(
            SQLiteStore(config.app.database_path),
            cash=args.cash,
            reset=args.reset,
        )
        print(f"cash={account.cash:.2f}")
        print(f"initial_cash={account.initial_cash:.2f}")
        print(f"positions={len(account.positions)}")
        print(f"updated_at={account.updated_at}")
        return 0

    if args.command == "paper-status":
        try:
            status = get_paper_status(config, SQLiteStore(config.app.database_path))
        except KISClientError as exc:
            print(f"Paper status error: {exc}", file=sys.stderr)
            return 1
        print(f"cash={status.cash:.2f}")
        print(f"positions_value={status.positions_value:.2f}")
        print(f"total_equity={status.total_equity:.2f}")
        print(f"positions={len(status.positions)}")
        for position in status.positions:
            print(
                f"{position.symbol}: qty={position.quantity} units={position.units} "
                f"avg={position.average_price:.2f} last_entry={position.last_entry_price}"
            )
        return 0

    if args.command == "paper-plan-day":
        try:
            result = create_paper_daily_plan(
                config,
                SQLiteStore(config.app.database_path),
                trade_date=args.trade_date,
            )
        except KISClientError as exc:
            print(f"Paper plan error: {exc}", file=sys.stderr)
            return 1
        actionable = [signal for signal in result.signals if signal.quantity > 0]
        print(f"trade_date={result.trade_date}")
        print(f"cash={result.cash:.2f}")
        print(f"total_equity={result.total_equity:.2f}")
        print(f"signals={len(result.signals)}")
        print(f"actionable_signals={len(actionable)}")
        print(f"saved_candidates={result.saved_candidates}")
        print("approval_message_start")
        print(result.approval_message)
        print("approval_message_end")
        return 0

    if args.command == "paper-execute":
        try:
            result = execute_paper_candidates(
                config,
                SQLiteStore(config.app.database_path),
                trade_date=args.trade_date,
            )
        except KISClientError as exc:
            print(f"Paper execution error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={result.trade_date}")
        print(f"executions={len(result.executions)}")
        for execution in result.executions:
            print(
                f"{execution.status} {execution.symbol} {execution.action} "
                f"qty={execution.quantity} price={execution.price:.2f} "
                f"notional={execution.notional:.2f} message={execution.message}"
            )
        print(f"cash={result.cash:.2f}")
        print(f"total_equity={result.total_equity:.2f}")
        return 0

    if args.command == "paper-run-day":
        run_date = args.trade_date or default_us_trade_date()
        if not args.force and not is_trading_day(run_date):
            print(f"trade_date={run_date}")
            print("skipped=market_closed")
            return 0
        if args.after_open_only and not is_after_regular_market_open(buffer_minutes=5):
            print(f"trade_date={run_date}")
            print("skipped=market_not_open")
            return 0
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        paper_run_state_key = f"paper_run_day_completed:{run_date}"
        if args.once_per_day and store.get_state(paper_run_state_key):
            print(f"trade_date={run_date}")
            print("skipped=already_completed")
            return 0
        try:
            result = run_paper_day(
                config,
                store,
                trade_date=run_date,
                send_report=args.send_report,
            )
        except (KISClientError, TelegramClientError) as exc:
            print(f"Paper run error: {exc}", file=sys.stderr)
            return 1
        if args.once_per_day:
            store.set_state(paper_run_state_key, datetime.now(timezone.utc).isoformat())
        print(f"trade_date={result.trade_date}")
        print(f"signals={len(result.plan.signals)}")
        print(f"actionable_signals={len([signal for signal in result.plan.signals if signal.quantity > 0])}")
        print(f"executions={len(result.execution.executions)}")
        print(f"cash={result.execution.cash:.2f}")
        print(f"total_equity={result.execution.total_equity:.2f}")
        print(f"sent={result.sent}")
        print("report_message_start")
        print(result.report_message)
        print("report_message_end")
        return 0

    if args.command == "request-approval":
        try:
            result = request_approval(config, trade_date=args.trade_date, equity_override=args.equity)
        except (KISClientError, TelegramClientError) as exc:
            print(f"Approval request error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={result.trade_date}")
        print(f"candidates_count={result.candidates_count}")
        print(f"sent={result.sent}")
        print(result.message)
        return 0

    if args.command == "collect-approval":
        try:
            result = collect_approval(config, trade_date=args.trade_date, timeout=args.timeout)
        except TelegramClientError as exc:
            print(f"Approval collect error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={result.trade_date}")
        print(f"updates_seen={result.updates_seen}")
        print(f"approvals_recorded={result.approvals_recorded}")
        print(f"status={result.status.value}")
        print(f"response_text={result.response_text}")
        return 0

    if args.command == "collect-final-approval":
        try:
            result = collect_final_approval(config, trade_date=args.trade_date, timeout=args.timeout)
        except TelegramClientError as exc:
            print(f"Final approval collect error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={result.trade_date}")
        print(f"updates_seen={result.updates_seen}")
        print(f"approvals_recorded={result.approvals_recorded}")
        print(f"status={result.status.value}")
        print(f"response_text={result.response_text}")
        return 0

    if args.command == "close-report":
        try:
            result = create_close_report(
                config,
                report_date=args.report_date,
                send=args.send,
                local_only=args.local_only,
            )
        except (KISClientError, TelegramClientError) as exc:
            print(f"Close report error: {exc}", file=sys.stderr)
            return 1
        print(f"report_date={result.report_date}")
        print(f"report_id={result.report_id}")
        print(f"filled_count={result.filled_count}")
        print(f"pending_count={result.pending_count}")
        print(f"failed_count={result.failed_count}")
        print(f"sent={result.sent}")
        print("message_start")
        print(result.message)
        print("message_end")
        return 0

    if args.command == "execute-approved":
        trade_date = args.trade_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        if args.validate:
            try:
                validations = validate_approved_candidates(KISClient(config.broker), store, trade_date)
            except KISClientError as exc:
                print(f"Validation error: {exc}", file=sys.stderr)
                return 1
            for validation in validations:
                print(
                    f"validation {validation.candidate.symbol} {validation.candidate.action} "
                    f"ok={validation.ok} message={validation.message}"
                )
            results = execute_candidates_dry_run(
                store,
                [validation.candidate for validation in validations if validation.ok],
            )
            has_failure = any(not validation.ok for validation in validations)
        else:
            results = execute_approved_dry_run(store, trade_date)
            has_failure = False
        print(f"trade_date={trade_date}")
        print(f"executed_count={len(results)}")
        for result in results:
            print(f"{result.idempotency_key}: {result.status} {result.message}")
        return 1 if has_failure else 0

    if args.command == "execute-final-approved":
        trade_date = args.trade_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        results = execute_final_approved_dry_run(store, trade_date)
        print(f"trade_date={trade_date}")
        print(f"executed_count={len(results)}")
        for result in results:
            print(f"{result.idempotency_key}: {result.status} {result.message}")
        return 0

    if args.command == "execute-live-approved":
        trade_date = args.trade_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        try:
            results = execute_final_approved_live_orders(
                config=config,
                store=store,
                client=KISClient(config.broker),
                trade_date=trade_date,
                confirmation=args.confirm,
                max_price_deviation=args.max_price_deviation,
            )
        except (KISClientError, OrderExecutionError) as exc:
            print(f"Live execution blocked: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={trade_date}")
        print(f"submitted_count={len(results)}")
        for result in results:
            print(f"{result.idempotency_key}: {result.status} broker_order_id={result.broker_order_id or '-'}")
        return 0

    if args.command == "validate-approved":
        trade_date = args.trade_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        try:
            review = build_final_pretrade_review(
                config,
                store,
                KISClient(config.broker),
                trade_date,
                max_price_deviation=args.max_price_deviation,
            )
        except KISClientError as exc:
            print(f"Validation error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={trade_date}")
        validations = review.validations
        print(f"validations={len(validations)}")
        for validation in validations:
            print(
                f"{validation.candidate.symbol} {validation.candidate.action} "
                f"qty={validation.candidate.quantity} ok={validation.ok} "
                f"latest={validation.latest_price} approved={validation.approved_price} "
                f"notional={validation.notional:.2f} message={validation.message}"
            )
        print("review_message_start")
        print(review.message)
        print("review_message_end")
        if args.send:
            try:
                TelegramClient(config.telegram).send_message(review.message)
            except TelegramClientError as exc:
                print(f"Telegram error: {exc}", file=sys.stderr)
                return 1
            print("sent=True")
        return 0 if all(validation.ok for validation in validations) else 1

    if args.command == "final-pretrade":
        try:
            result = run_final_pretrade(
                config,
                trade_date=args.trade_date,
                max_price_deviation=args.max_price_deviation,
                send=args.send,
                collect_timeout=args.collect_timeout,
                force=args.force,
            )
        except (KISClientError, TelegramClientError) as exc:
            print(f"Final pretrade error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={result.trade_date}")
        print(f"validations={len(result.review.validations)}")
        print(f"cash_plan_included={result.review.cash_plan_included}")
        print(f"sent={result.sent}")
        print("review_message_start")
        print(result.review.message)
        print("review_message_end")
        if result.final_collect is not None:
            print(f"updates_seen={result.final_collect.updates_seen}")
            print(f"approvals_recorded={result.final_collect.approvals_recorded}")
            print(f"final_approval_status={result.final_collect.status.value}")
        else:
            print("final_collect=skipped")
        return 0 if all(validation.ok for validation in result.review.validations) else 1

    if args.command == "cash-plan":
        trade_date = args.trade_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        client = KISClient(config.broker)
        try:
            validations = validate_approved_candidates(client, store, trade_date)
            if args.cash is None:
                balance = sync_overseas_balance(client, store)
                cash = balance.cash
            else:
                cash = args.cash
            parking_price = args.parking_price
            parking_quantity = parking_quantity_for_config(store, config.cash)
            should_fetch_price = parking_price is None and should_fetch_parking_price(
                cash,
                validations,
                config.cash.min_cash_buffer,
                config.cash.parking_buy_threshold,
            )
            if should_fetch_price:
                parking_symbol = config.cash.parking_etfs[0]
                parking_exchange = config.strategy.exchange_by_symbol.get(parking_symbol, "NAS")
                candles = fetch_daily_candles(client, parking_symbol, parking_exchange)
                parking_price = candles[-1].close if candles else None
        except KISClientError as exc:
            print(f"Cash plan error: {exc}", file=sys.stderr)
            return 1
        plan = build_cash_plan(
            cash=cash,
            parking_quantity=parking_quantity,
            parking_price=parking_price,
            validations=validations,
            config=config.cash,
        )
        print(f"trade_date={trade_date}")
        print(format_cash_plan(plan))
        return 0

    if args.command == "tax-harvest-report":
        year = args.year or int(default_us_trade_date()[:4])
        store = SQLiteStore(config.app.database_path)
        try:
            latest_prices = parse_price_overrides(args.price)
            report = build_tax_harvest_report(
                config=config,
                store=store,
                year=year,
                latest_prices=latest_prices or None,
                usd_krw=args.usd_krw,
            )
        except (KISClientError, ValueError) as exc:
            print(f"Tax harvest report error: {exc}", file=sys.stderr)
            return 1
        message = build_tax_harvest_message(report)
        print(f"year={report.year}")
        print(f"realized_gain_krw={report.realized_gain_krw:.0f}")
        print(f"remaining_target_krw={report.remaining_target_krw:.0f}")
        print(f"open_lots={len(report.open_lots)}")
        print(f"candidates={len(report.candidates)}")
        print("message_start")
        print(message)
        print("message_end")
        if args.send:
            try:
                TelegramClient(config.telegram).send_message(message)
            except TelegramClientError as exc:
                print(f"Telegram error: {exc}", file=sys.stderr)
                return 1
            print("sent=True")
        return 0

    if args.command == "pre-market":
        try:
            result = run_pre_market(
                config,
                trade_date=args.trade_date,
                equity_override=args.equity,
                collect_timeout=args.collect_timeout,
                force=args.force,
            )
        except (KISClientError, TelegramClientError) as exc:
            print(f"Pre-market error: {exc}", file=sys.stderr)
            return 1
        print(f"trade_date={result.trade_date}")
        print(f"candidates_count={result.approval_request.candidates_count}")
        print(f"approval_sent={result.approval_request.sent}")
        print(f"message={result.approval_request.message}")
        if result.approval_collect is not None:
            print(f"updates_seen={result.approval_collect.updates_seen}")
            print(f"approvals_recorded={result.approval_collect.approvals_recorded}")
            print(f"approval_status={result.approval_collect.status.value}")
        else:
            print("approval_collect=skipped")
        return 0

    if args.command == "market-close":
        try:
            result = run_market_close(
                config,
                report_date=args.report_date,
                send_report=args.send_report,
                force=args.force,
                local_only=args.local_only,
            )
        except (KISClientError, TelegramClientError) as exc:
            print(f"Market-close error: {exc}", file=sys.stderr)
            return 1
        print(f"report_date={result.report_date}")
        print(f"dry_run_executed={len(result.execution_results)}")
        print(f"report_id={result.close_report.report_id}")
        print(f"filled_count={result.close_report.filled_count}")
        print(f"pending_count={result.close_report.pending_count}")
        print(f"report_sent={result.close_report.sent}")
        print(f"message={result.close_report.message}")
        return 0

    if args.command == "post-market":
        try:
            result = run_post_market(
                config,
                report_date=args.report_date,
                send_report=args.send_report,
                force=args.force,
                local_only=args.local_only,
                rollover=not args.no_rollover,
                rollover_target_date=args.rollover_target_date,
            )
        except (KISClientError, TelegramClientError) as exc:
            print(f"Post-market error: {exc}", file=sys.stderr)
            return 1
        print(f"report_date={result.report_date}")
        print(f"dry_run_executed={len(result.market_close.execution_results)}")
        print(f"report_id={result.market_close.close_report.report_id}")
        print(f"filled_count={result.market_close.close_report.filled_count}")
        print(f"pending_count={result.market_close.close_report.pending_count}")
        print(f"report_sent={result.market_close.close_report.sent}")
        if result.rollover is None:
            print("rollover=skipped")
        else:
            print(f"rollover_source_date={result.rollover.source_date}")
            print(f"rollover_target_date={result.rollover.target_date}")
            print(f"rollover_candidates_found={result.rollover.candidates_found}")
            print(f"rollover_candidates_created={result.rollover.candidates_created}")
        print(f"message={result.market_close.close_report.message}")
        return 0

    if args.command == "calendar":
        value = args.date or default_us_trade_date()
        trading = is_trading_day(value)
        print(f"date={value}")
        print(f"is_trading_day={trading}")
        print(f"next_trading_day={next_trading_day(value).isoformat()}")
        return 0

    if args.command == "safety-check":
        status = check_safety(config)
        print(f"broker_live={status.broker_live}")
        print(f"app_live={status.app_live}")
        print(f"live_order_enabled={status.live_order_enabled}")
        print(f"message={status.message}")
        return 0

    if args.command == "status":
        status = get_runtime_status(config, trade_date=args.date)
        print(f"trade_date={status.trade_date}")
        print(f"is_trading_day={status.is_trading_day}")
        print(f"next_trading_day={status.next_trading_day}")
        print(f"broker_live={status.safety.broker_live}")
        print(f"app_live={status.safety.app_live}")
        print(f"live_order_enabled={status.safety.live_order_enabled}")
        for table, count in status.table_counts.items():
            print(f"{table}={count}")
        return 0

    if args.command == "rehearse-local":
        result = run_local_rehearsal(SQLiteStore(config.app.database_path), args.trade_date)
        print(f"trade_date={result.trade_date}")
        print(f"idempotency_key={result.idempotency_key}")
        print(f"candidate_created={result.candidate_created}")
        print(f"approvals_recorded={result.approvals_recorded}")
        print(f"first_execution_count={result.first_execution_count}")
        print(f"second_execution_count={result.second_execution_count}")
        return 0

    if args.command == "orders":
        trade_date = args.trade_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        store.initialize()
        statuses = store.list_order_statuses(trade_date)
        print(f"trade_date={trade_date}")
        print(f"orders_count={len(statuses)}")
        for status in statuses:
            print(
                f"{status.id}: {status.symbol} {status.action} qty={status.quantity} "
                f"reason={status.reason} approval={status.approval_status or '-'} "
                f"final={status.final_approval_status or '-'} "
                f"event={status.event_status or '-'} key={status.idempotency_key}"
            )
        return 0

    if args.command == "rollover-pending":
        source_date = args.source_date or default_us_trade_date()
        store = SQLiteStore(config.app.database_path)
        result = rollover_pending_candidates(store, source_date=source_date, target_date=args.target_date)
        print(f"source_date={result.source_date}")
        print(f"target_date={result.target_date}")
        print(f"candidates_found={result.candidates_found}")
        print(f"candidates_created={result.candidates_created}")
        return 0

    if args.command == "backup-db":
        result = backup_file(config.app.database_path, args.backup_dir)
        print(f"source={result.source}")
        print(f"destination={result.destination}")
        print(f"copied={result.copied}")
        return 0 if result.copied else 1

    if args.command == "commands":
        print(format_commands())
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def should_fetch_parking_price(
    cash: float,
    validations,
    min_cash_buffer: float,
    parking_buy_threshold: float,
) -> bool:
    buy_notional = sum(validation.notional for validation in validations if should_include_buy_notional(validation))
    sell_notional = sum(
        validation.notional for validation in validations if validation.ok and validation.candidate.action == "SELL"
    )
    return should_fetch_parking_price_for_amounts(
        cash,
        buy_notional,
        sell_notional,
        min_cash_buffer,
        parking_buy_threshold,
    )
