# Windows Scheduler

This project can run the paper trading rehearsal from Windows Task Scheduler.

## Schedule

- Task name: `TurtleInvest-PaperRunDay`
- Local PC time: `22:35` and `23:35`
- Days: Monday to Friday
- Catch-up: run once at user logon
- Behavior: wake the PC from sleep, run paper trading after the US market has opened, send the Telegram after-report.
- Command: `python -m turtle_invest paper-run-day --after-open-only --once-per-day --send-report --config config.local.json`

The CLI skips automatically when the US market calendar says the date is closed. It also skips before 09:35 US Eastern time, so daylight saving time is handled by the `America/New_York` timezone instead of by a fixed Korean clock time.

Two wake times are registered:

- `22:35` KST covers US daylight saving time, when 09:35 ET is 22:35 KST.
- `23:35` KST covers US standard time, when 09:35 ET is 23:35 KST.

`--once-per-day` prevents the second trigger from running the same trade date again.

The logon catch-up trigger is a backup for cases where Windows did not wake the PC at the scheduled time, or the PC was powered off. If the US market has not opened yet, it exits with `skipped=market_not_open`.

## Register

Run this from the project root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_paper_scheduler.ps1
```

To choose different local times:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_paper_scheduler.ps1 -At 22:40,23:40
```

## Logs

Each scheduled run writes a transcript under:

```text
data\logs\
```

## Windows Sleep Settings

The task is registered with `WakeToRun=true`, but Windows must allow wake timers:

1. Open Control Panel.
2. Go to Power Options.
3. Open the active plan's advanced settings.
4. Set Sleep > Allow wake timers to Enable.

On laptops, Windows or firmware settings can still block wake timers on battery.
