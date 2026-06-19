# Pending Order Rollover

`rollover-pending` copies local candidates that were not completed into the next trading day.

## Command

Run market-close reporting and rollover together:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest post-market --config config.local.json
```

Send the close report and then roll over pending candidates:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest post-market --send-report --config config.local.json
```

Run rollover only:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest rollover-pending --source-date 2026-06-12 --config config.local.json
```

To choose the target date manually:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest rollover-pending --source-date 2026-06-12 --target-date 2026-06-15 --config config.local.json
```

## Rollover Rules

- Candidates with no local execution event are rolled over.
- Candidates whose latest local event is `FAILED`, `REJECTED`, `BLOCKED`, or `PENDING` are rolled over.
- Candidates with `DRY_RUN`, `SUBMITTED`, or `FILLED` events are not rolled over automatically.
- Rollover candidates do not inherit prior approvals. They must start from approval again.
- Rollover is idempotent for the same source and target date.

## Safety Note

`SUBMITTED` is not automatically rolled over because it may correspond to a real broker order. Market-close reconciliation updates matched submitted orders to `FILLED` or `PENDING` using broker fill/unfilled rows. Only reconciled `PENDING` orders are eligible for rollover.
