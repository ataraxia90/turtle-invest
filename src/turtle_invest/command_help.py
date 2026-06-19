from __future__ import annotations


COMMANDS = [
    ("status", "Show calendar, safety, and DB counts."),
    ("safety-check", "Verify live order execution is locked or enabled."),
    ("calendar", "Check whether a US date is a trading day."),
    ("paper-init", "Initialize local paper account cash."),
    ("paper-status", "Show local paper account and marked equity."),
    ("paper-plan-day", "Create paper candidates from KIS prices and strategy."),
    ("paper-execute", "Fill paper candidates and update local portfolio."),
    ("paper-run-day", "Run paper plan, execution, and optional Telegram report."),
    ("refresh-universe", "Store the active annual universe snapshot."),
    ("show-universe", "Show the active universe used by planning."),
    ("pre-market", "Run pre-market planning and approval request workflow."),
    ("collect-approval", "Read Telegram approval responses."),
    ("validate-approved", "Re-check approved candidates before execution."),
    ("final-pretrade", "Build/send final pre-trade review and collect final approval."),
    ("collect-final-approval", "Read Telegram final execution approval responses."),
    ("cash-plan", "Plan parking ETF cash actions around approved orders."),
    ("tax-harvest-report", "Estimate annual overseas-stock gain harvesting room."),
    ("execute-approved", "Record approved candidates as DRY_RUN events."),
    ("execute-final-approved", "Record final-approved candidates as DRY_RUN events."),
    ("execute-live-approved", "Submit final-approved candidates as live KIS orders."),
    ("market-close", "Run dry-run execution and close report workflow."),
    ("post-market", "Run market-close report and pending rollover workflow."),
    ("rollover-pending", "Copy unexecuted or failed candidates to the next trading day."),
    ("orders", "List local order candidates and execution status."),
    ("rehearse-local", "Run a local-only approval and dry-run rehearsal."),
    ("backup-db", "Copy local SQLite DB to data/backups."),
]


def format_commands() -> str:
    width = max(len(command) for command, _ in COMMANDS)
    return "\n".join(f"{command.ljust(width)}  {description}" for command, description in COMMANDS)
