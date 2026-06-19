# Local Paper Trading

This mode uses KIS only for market data. Cash, positions, fills, and portfolio equity are stored locally.

## Initialize

Start with 10,000 USD:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest paper-init --cash 10000 --config config.local.json
```

Use `--reset` only when you intentionally want to wipe the local paper portfolio:

```powershell
python -m turtle_invest paper-init --cash 10000 --reset --config config.local.json
```

## Daily Flow

Run the full paper routine and send only an after-the-fact Telegram report:

```powershell
python -m turtle_invest paper-run-day --send-report --config config.local.json
```

Before the US market opens, create the strategy plan from the local paper account:

```powershell
python -m turtle_invest paper-plan-day --config config.local.json
```

After the market opens and KIS has current daily open data, fill paper candidates at the market-price assumption:

```powershell
python -m turtle_invest paper-execute --config config.local.json
```

Check the local paper portfolio:

```powershell
python -m turtle_invest paper-status --config config.local.json
```

Paper trading does not ask for Telegram pre-approval. Telegram is used only for after-the-fact reporting in `paper-run-day --send-report`.

## Fill Assumption

- If the latest KIS daily candle date matches the trade date, paper orders fill at that candle's open.
- Otherwise, paper orders fill at the latest close.
- BUY decreases local paper cash and creates or increases a local paper position.
- SELL increases local paper cash and reduces or removes the local paper position.
- No KIS order API is called.

## Portfolio Risk Caps

The strategy first calculates Turtle ATR unit size, then caps BUY quantity with these portfolio limits:

- New BUY notional: max 15% of total equity.
- Single-symbol total exposure after the order: max 25% of total equity.
- Total stock exposure after the order: max 95% of total equity.
- Single-symbol stop-risk exposure: max 3% of total equity.
- Total stop-risk exposure: max 6% of total equity.

With a 10,000 USD paper account, this means a new order is capped at about 1,500 USD before the other risk limits are checked.

## Notes

- Paper candidates are stored with `paper=true` in the payload and `PAPER` in the idempotency key.
- Paper fills are recorded as local order events with `PAPER_FILLED` or `PAPER_BLOCKED`.
- The live account balance and live positions are not modified.
