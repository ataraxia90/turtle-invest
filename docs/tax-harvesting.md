# Tax Harvesting Report

This module estimates how much overseas-stock gain can be realized while staying under the Korean annual basic deduction target.

It is report-only. It does not create orders.

## Policy

- Annual overseas-stock deduction setting: `tax.annual_exemption_krw`
- Operating target with safety margin: `tax.harvest_target_krw`
- Default estimate: 2,500,000 KRW exemption and 2,350,000 KRW target

Actual tax reporting should be checked against broker tax documents and a tax professional.

## Command

Use the configured fallback FX rate and fetch latest prices:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest tax-harvest-report --config config.local.json
```

Use a specific FX rate and manual prices:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest tax-harvest-report --year 2026 --usd-krw 1350 --price BRK/B=497.60 --config config.local.json
```

Send the report to Telegram:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest tax-harvest-report --send --config config.local.json
```

## Current Accounting

The first implementation reconstructs a FIFO lot book from local `PAPER_FILLED` events.

For live trading, the same report should be extended to ingest broker fills and broker tax data before it is used for real order decisions.
