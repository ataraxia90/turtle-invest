# Render + Supabase deployment

This project can run on Render Cron Jobs with Supabase Postgres as the persistent state store.

## Why Supabase

Render Cron Jobs do not provide a persistent disk. The app stores idempotency keys, Telegram offsets,
approval state, paper-trading state, and order events in the configured store. Use Supabase Postgres
for Render so scheduled runs share the same durable state.

## Supabase setup

1. Create a Supabase project.
2. Open the project dashboard and click Connect.
3. Copy the Session Pooler connection string.
4. Replace the password placeholder with the database password.
5. Keep the full connection string private.

Session Pooler is recommended for Render because it is IPv4-compatible and works well for short CLI
runs. Use a string shaped like:

```text
postgres://postgres.<project-ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres
```

## Render Cron Job

Create a Render Cron Job from the GitHub repository.

```text
Repository: ataraxia90/turtle-invest
Branch: main
Runtime: Python 3
Build Command: pip install -e .
Command: python -m turtle_invest paper-run-day --after-open-only --once-per-day --send-report --config /etc/secrets/config.local.json
Schedule: 35 13,14 * * MON-FRI
```

Render schedules are UTC. `35 13,14 * * MON-FRI` covers 22:35 and 23:35 KST, matching US daylight
saving and standard time. The CLI also checks the US market calendar and `--after-open-only`.

## Render environment variables

Set these in the Cron Job's Environment page:

```text
DATABASE_URL=<Supabase Session Pooler connection string>
KIS_APP_KEY=<KIS app key>
KIS_APP_SECRET=<KIS app secret>
TELEGRAM_BOT_TOKEN=<Telegram bot token>
TELEGRAM_CHAT_ID=<Telegram chat id>
```

## Render secret file

Add a secret file:

```text
Filename: config.local.json
```

Use your local config as the starting point, but set the app database fields like this:

```json
{
  "app": {
    "env": "paper",
    "timezone": "Asia/Seoul",
    "log_level": "INFO",
    "database_provider": "postgres",
    "database_url_env": "DATABASE_URL"
  }
}
```

Keep the rest of your broker, Telegram, strategy, cash, and tax settings from your local
`config.local.json`. Do not paste actual secret values into the JSON if they are provided through
environment variables.

## First run checklist

1. Trigger `python -m turtle_invest init-db --config /etc/secrets/config.local.json` once from the
   Render shell or by temporarily setting the Cron command to that command and clicking Trigger Run.
2. Trigger `python -m turtle_invest doctor --config /etc/secrets/config.local.json`.
3. Trigger `python -m turtle_invest status --config /etc/secrets/config.local.json`.
4. Restore the Cron command to `paper-run-day`.
5. Watch the first scheduled run logs.

## Local behavior

Local development still defaults to SQLite:

```json
"database_provider": "sqlite",
"database_path": "data/turtle_invest.db"
```

`backup-db` is only for SQLite. Use Supabase backups for the Postgres database.
