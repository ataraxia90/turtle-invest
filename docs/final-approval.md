# Final Approval Gate

The system keeps strategy approval and final execution approval separate.

## Approval Stages

- `strategy`: user approved the strategy-generated order candidates.
- `final`: user approved execution after pre-trade validation and cash planning.

Existing approvals are treated as `strategy` approvals during database migration.

## Commands

Collect initial strategy approval:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest collect-approval --config config.local.json
```

Send final pre-trade review:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest validate-approved --send --config config.local.json
```

Or use the combined final pre-trade command:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest final-pretrade --send --config config.local.json
```

To send/review and immediately wait for a final approval response:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest final-pretrade --send --collect-timeout 60 --config config.local.json
```

Collect final execution approval:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest collect-final-approval --config config.local.json
```

Dry-run only final-approved candidates:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest execute-final-approved --config config.local.json
```

Submit final-approved candidates as live KIS orders:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest execute-live-approved --confirm I_UNDERSTAND_LIVE_ORDERS --config config.local.json
```

## Safety

- `market-close` now uses the final-approved dry-run path.
- `execute-final-approved` ignores candidates that only have strategy approval.
- `execute-live-approved` requires final approval, `app.env=live`, `broker.mode=live`, and exact confirmation text.
- `execute-live-approved` revalidates latest price, cash, and holdings immediately before submitting orders.
- If any final-approved candidate fails live preflight validation, live submission is blocked for the batch.
- `final-pretrade` skips non-trading days unless `--force` is supplied.
- Live order execution should use final-approved candidates only.
