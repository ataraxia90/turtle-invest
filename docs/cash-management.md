# Cash Management

This module plans parking ETF actions around approved strategy orders. It does not place orders.

## Command

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest cash-plan --config config.local.json
```

For local-only checks without broker price reads:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest cash-plan --cash 1200 --parking-price 100 --config config.local.json
```

## Rules

- Approved BUY notional reduces available cash.
- Approved SELL notional increases available cash.
- If available cash after approved orders is below `cash.min_cash_buffer`, the plan proposes selling the primary parking ETF.
- If available cash is above `cash.min_cash_buffer` and `cash.parking_buy_threshold`, the plan proposes buying the primary parking ETF.
- The current primary parking ETF is the first symbol in `cash.parking_etfs`.

## Safety

- `cash-plan` is read-only except for the existing balance sync snapshot when live broker cash is queried.
- It never submits KIS orders.
- Live order execution remains gated separately by `app.env=live` and `broker.mode=live`.
