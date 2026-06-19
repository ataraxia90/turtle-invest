from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, Union

from turtle_invest.config import Settings


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StoredOrderCandidate:
    trade_date: str
    symbol: str
    action: str
    quantity: int
    reason: str
    idempotency_key: str
    payload: dict[str, Any]
    id: Optional[int] = None


@dataclass(frozen=True)
class StoredOrderStatus:
    id: int
    trade_date: str
    symbol: str
    action: str
    quantity: int
    reason: str
    idempotency_key: str
    approval_status: Optional[str]
    final_approval_status: Optional[str]
    event_status: Optional[str]


@dataclass(frozen=True)
class StoredUniverseMember:
    universe_date: str
    symbol: str
    rank: int
    market_cap: Optional[float]
    source: str


class Store(Protocol):
    def initialize(self) -> None: ...
    def record_account_snapshot(self, captured_at: str, total_equity: float, cash: float, payload: dict[str, Any]) -> int: ...
    def upsert_position(self, symbol: str, quantity: int, units: int, last_entry_price: Optional[float], updated_at: str) -> None: ...
    def get_position(self, symbol: str) -> Optional[Any]: ...
    def list_positions(self) -> list[Any]: ...
    def replace_universe_members(self, universe_date: str, members: list[StoredUniverseMember]) -> int: ...
    def latest_universe_date(self) -> Optional[str]: ...
    def list_universe_members(self, universe_date: Optional[str] = None) -> list[StoredUniverseMember]: ...
    def record_order_candidate(self, candidate: StoredOrderCandidate) -> bool: ...
    def list_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]: ...
    def list_unapproved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]: ...
    def list_approved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]: ...
    def list_final_unapproved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]: ...
    def list_final_approved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]: ...
    def record_approval(self, order_candidate_id: int, status: str, responded_at: str, response_text: str, stage: str = "strategy") -> int: ...
    def record_order_event(
        self,
        order_candidate_id: Optional[int],
        broker_order_id: Optional[str],
        status: str,
        occurred_at: str,
        payload: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> int: ...
    def has_order_event(self, idempotency_key: str) -> bool: ...
    def update_order_event_status(self, idempotency_key: str, status: str, occurred_at: str, payload: dict[str, Any]) -> bool: ...
    def list_order_events_for_trade_date(self, trade_date: str) -> list[Any]: ...
    def list_paper_filled_order_events(self) -> list[Any]: ...
    def list_submitted_order_events_for_trade_date(self, trade_date: str) -> list[Any]: ...
    def list_order_statuses(self, trade_date: str) -> list[StoredOrderStatus]: ...
    def list_rollover_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]: ...
    def record_report(self, report_date: str, report_type: str, payload: dict[str, Any], sent_at: Optional[str] = None) -> int: ...
    def get_state(self, key: str) -> Optional[str]: ...
    def set_state(self, key: str, value: str) -> None: ...


def create_store(config: Settings) -> Store:
    if config.app.database_provider == "postgres":
        database_url = os.environ.get(config.app.database_url_env)
        if not database_url:
            raise RuntimeError(f"missing environment variable: {config.app.database_url_env}")
        return PostgresStore(database_url)
    return SQLiteStore(config.app.database_path)


class SQLiteStore:
    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        if self.path.parent:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            ensure_column(connection, "order_events", "idempotency_key", "TEXT")
            ensure_column(connection, "approvals", "stage", "TEXT NOT NULL DEFAULT 'strategy'")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_order_events_idempotency_key
                ON order_events(idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version)
                VALUES (?)
                ON CONFLICT(version) DO NOTHING
                """,
                (SCHEMA_VERSION,),
            )

    def record_account_snapshot(
        self,
        captured_at: str,
        total_equity: float,
        cash: float,
        payload: dict[str, Any],
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO account_snapshots(captured_at, total_equity, cash, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (captured_at, total_equity, cash, to_json(payload)),
            )
            return int(cursor.lastrowid)

    def upsert_position(
        self,
        symbol: str,
        quantity: int,
        units: int,
        last_entry_price: Optional[float],
        updated_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO positions(symbol, quantity, units, last_entry_price, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    units = excluded.units,
                    last_entry_price = excluded.last_entry_price,
                    updated_at = excluded.updated_at
                """,
                (symbol, quantity, units, last_entry_price, updated_at),
            )

    def get_position(self, symbol: str) -> Optional[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()

    def list_positions(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM positions ORDER BY symbol").fetchall()

    def replace_universe_members(self, universe_date: str, members: list[StoredUniverseMember]) -> int:
        with self.connect() as connection:
            connection.execute("DELETE FROM universe_members WHERE universe_date = ?", (universe_date,))
            connection.executemany(
                """
                INSERT INTO universe_members(universe_date, symbol, rank, market_cap, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (member.universe_date, member.symbol, member.rank, member.market_cap, member.source)
                    for member in members
                ],
            )
            return len(members)

    def latest_universe_date(self) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(universe_date) AS universe_date FROM universe_members").fetchone()
        if row is None or row["universe_date"] is None:
            return None
        return str(row["universe_date"])

    def list_universe_members(self, universe_date: Optional[str] = None) -> list[StoredUniverseMember]:
        selected_date = universe_date or self.latest_universe_date()
        if selected_date is None:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT universe_date, symbol, rank, market_cap, source
                FROM universe_members
                WHERE universe_date = ?
                ORDER BY rank
                """,
                (selected_date,),
            ).fetchall()
        return [
            StoredUniverseMember(
                universe_date=str(row["universe_date"]),
                symbol=str(row["symbol"]),
                rank=int(row["rank"]),
                market_cap=row["market_cap"],
                source=str(row["source"]),
            )
            for row in rows
        ]

    def record_order_candidate(self, candidate: StoredOrderCandidate) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO order_candidates(
                    trade_date,
                    symbol,
                    action,
                    quantity,
                    reason,
                    idempotency_key,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.trade_date,
                    candidate.symbol,
                    candidate.action,
                    candidate.quantity,
                    candidate.reason,
                    candidate.idempotency_key,
                    to_json(candidate.payload),
                ),
            )
            return cursor.rowcount == 1

    def list_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM order_candidates
                WHERE trade_date = ?
                ORDER BY id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderCandidate(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def list_unapproved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.stage = 'strategy'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderCandidate(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def list_approved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = ?
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'strategy'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderCandidate(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def list_final_unapproved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = ?
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'strategy'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.stage = 'final'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderCandidate(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def list_final_approved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = ?
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'strategy'
                  )
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'final'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderCandidate(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def record_approval(
        self,
        order_candidate_id: int,
        status: str,
        responded_at: str,
        response_text: str,
        stage: str = "strategy",
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO approvals(order_candidate_id, status, responded_at, response_text, stage)
                VALUES (?, ?, ?, ?, ?)
                """,
                (order_candidate_id, status, responded_at, response_text, stage),
            )
            return int(cursor.lastrowid)

    def record_order_event(
        self,
        order_candidate_id: Optional[int],
        broker_order_id: Optional[str],
        status: str,
        occurred_at: str,
        payload: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO order_events(
                    order_candidate_id,
                    broker_order_id,
                    status,
                    occurred_at,
                    payload_json,
                    idempotency_key
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_candidate_id, broker_order_id, status, occurred_at, to_json(payload), idempotency_key),
            )
            return int(cursor.lastrowid)

    def has_order_event(self, idempotency_key: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM order_events WHERE idempotency_key = ? LIMIT 1",
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def update_order_event_status(
        self,
        idempotency_key: str,
        status: str,
        occurred_at: str,
        payload: dict[str, Any],
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_events
                SET status = ?, occurred_at = ?, payload_json = ?
                WHERE idempotency_key = ?
                """,
                (status, occurred_at, to_json(payload), idempotency_key),
            )
            return cursor.rowcount > 0

    def list_order_events_for_trade_date(self, trade_date: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM order_events
                WHERE idempotency_key LIKE ?
                ORDER BY id
                """,
                (f"{trade_date}:%",),
            ).fetchall()

    def list_paper_filled_order_events(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM order_events
                WHERE status = 'PAPER_FILLED'
                ORDER BY occurred_at, id
                """
            ).fetchall()

    def list_submitted_order_events_for_trade_date(self, trade_date: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM order_events
                WHERE idempotency_key LIKE ?
                  AND status = 'SUBMITTED'
                ORDER BY id
                """,
                (f"{trade_date}:%",),
            ).fetchall()

    def list_order_statuses(self, trade_date: str) -> list[StoredOrderStatus]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    oc.id,
                    oc.trade_date,
                    oc.symbol,
                    oc.action,
                    oc.quantity,
                    oc.reason,
                    oc.idempotency_key,
                    (
                        SELECT a.status
                        FROM approvals a
                        WHERE a.order_candidate_id = oc.id
                          AND a.stage = 'strategy'
                        ORDER BY a.id DESC
                        LIMIT 1
                    ) AS approval_status,
                    (
                        SELECT a.status
                        FROM approvals a
                        WHERE a.order_candidate_id = oc.id
                          AND a.stage = 'final'
                        ORDER BY a.id DESC
                        LIMIT 1
                    ) AS final_approval_status,
                    (
                        SELECT oe.status
                        FROM order_events oe
                        WHERE oe.idempotency_key = oc.idempotency_key
                        ORDER BY oe.id DESC
                        LIMIT 1
                    ) AS event_status
                FROM order_candidates oc
                WHERE oc.trade_date = ?
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderStatus(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                approval_status=row["approval_status"],
                final_approval_status=row["final_approval_status"],
                event_status=row["event_status"],
            )
            for row in rows
        ]

    def list_rollover_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = ?
                  AND (
                    NOT EXISTS (
                        SELECT 1
                        FROM order_events oe
                        WHERE oe.idempotency_key = oc.idempotency_key
                    )
                    OR (
                        SELECT oe.status
                        FROM order_events oe
                        WHERE oe.idempotency_key = oc.idempotency_key
                        ORDER BY oe.id DESC
                        LIMIT 1
                    ) IN ('FAILED', 'REJECTED', 'BLOCKED', 'PENDING')
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderCandidate(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def record_report(
        self,
        report_date: str,
        report_type: str,
        payload: dict[str, Any],
        sent_at: Optional[str] = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reports(report_date, report_type, payload_json, sent_at)
                VALUES (?, ?, ?, ?)
                """,
                (report_date, report_type, to_json(payload), sent_at),
            )
            return int(cursor.lastrowid)

    def get_state(self, key: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value),
            )


class PostgresStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Postgres storage requires installing psycopg[binary].") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def initialize(self) -> None:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(POSTGRES_SCHEMA_SQL)
                cursor.execute("ALTER TABLE order_events ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
                cursor.execute("ALTER TABLE approvals ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'strategy'")
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_order_events_idempotency_key
                    ON order_events(idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO schema_migrations(version)
                    VALUES (%s)
                    ON CONFLICT(version) DO NOTHING
                    """,
                    (SCHEMA_VERSION,),
                )

    def record_account_snapshot(
        self,
        captured_at: str,
        total_equity: float,
        cash: float,
        payload: dict[str, Any],
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO account_snapshots(captured_at, total_equity, cash, payload_json)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (captured_at, total_equity, cash, to_json(payload)),
            ).fetchone()
            return int(row["id"])

    def upsert_position(
        self,
        symbol: str,
        quantity: int,
        units: int,
        last_entry_price: Optional[float],
        updated_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO positions(symbol, quantity, units, last_entry_price, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    units = excluded.units,
                    last_entry_price = excluded.last_entry_price,
                    updated_at = excluded.updated_at
                """,
                (symbol, quantity, units, last_entry_price, updated_at),
            )

    def get_position(self, symbol: str) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM positions WHERE symbol = %s",
                (symbol,),
            ).fetchone()

    def list_positions(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM positions ORDER BY symbol").fetchall())

    def replace_universe_members(self, universe_date: str, members: list[StoredUniverseMember]) -> int:
        with self.connect() as connection:
            connection.execute("DELETE FROM universe_members WHERE universe_date = %s", (universe_date,))
            connection.executemany(
                """
                INSERT INTO universe_members(universe_date, symbol, rank, market_cap, source)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (member.universe_date, member.symbol, member.rank, member.market_cap, member.source)
                    for member in members
                ],
            )
            return len(members)

    def latest_universe_date(self) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(universe_date) AS universe_date FROM universe_members").fetchone()
        if row is None or row["universe_date"] is None:
            return None
        return str(row["universe_date"])

    def list_universe_members(self, universe_date: Optional[str] = None) -> list[StoredUniverseMember]:
        selected_date = universe_date or self.latest_universe_date()
        if selected_date is None:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT universe_date, symbol, rank, market_cap, source
                FROM universe_members
                WHERE universe_date = %s
                ORDER BY rank
                """,
                (selected_date,),
            ).fetchall()
        return [
            StoredUniverseMember(
                universe_date=str(row["universe_date"]),
                symbol=str(row["symbol"]),
                rank=int(row["rank"]),
                market_cap=row["market_cap"],
                source=str(row["source"]),
            )
            for row in rows
        ]

    def record_order_candidate(self, candidate: StoredOrderCandidate) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO order_candidates(
                    trade_date,
                    symbol,
                    action,
                    quantity,
                    reason,
                    idempotency_key,
                    payload_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(idempotency_key) DO NOTHING
                RETURNING id
                """,
                (
                    candidate.trade_date,
                    candidate.symbol,
                    candidate.action,
                    candidate.quantity,
                    candidate.reason,
                    candidate.idempotency_key,
                    to_json(candidate.payload),
                ),
            ).fetchone()
            return row is not None

    def list_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM order_candidates
                WHERE trade_date = %s
                ORDER BY id
                """,
                (trade_date,),
            ).fetchall()
        return rows_to_candidates(rows)

    def list_unapproved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = %s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.stage = 'strategy'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return rows_to_candidates(rows)

    def list_approved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = %s
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'strategy'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return rows_to_candidates(rows)

    def list_final_unapproved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = %s
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'strategy'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.stage = 'final'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return rows_to_candidates(rows)

    def list_final_approved_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = %s
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'strategy'
                  )
                  AND EXISTS (
                    SELECT 1
                    FROM approvals a
                    WHERE a.order_candidate_id = oc.id
                      AND a.status = 'approved'
                      AND a.stage = 'final'
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return rows_to_candidates(rows)

    def record_approval(
        self,
        order_candidate_id: int,
        status: str,
        responded_at: str,
        response_text: str,
        stage: str = "strategy",
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO approvals(order_candidate_id, status, responded_at, response_text, stage)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (order_candidate_id, status, responded_at, response_text, stage),
            ).fetchone()
            return int(row["id"])

    def record_order_event(
        self,
        order_candidate_id: Optional[int],
        broker_order_id: Optional[str],
        status: str,
        occurred_at: str,
        payload: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO order_events(
                    order_candidate_id,
                    broker_order_id,
                    status,
                    occurred_at,
                    payload_json,
                    idempotency_key
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (order_candidate_id, broker_order_id, status, occurred_at, to_json(payload), idempotency_key),
            ).fetchone()
            return int(row["id"])

    def has_order_event(self, idempotency_key: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM order_events WHERE idempotency_key = %s LIMIT 1",
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def update_order_event_status(
        self,
        idempotency_key: str,
        status: str,
        occurred_at: str,
        payload: dict[str, Any],
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_events
                SET status = %s, occurred_at = %s, payload_json = %s
                WHERE idempotency_key = %s
                """,
                (status, occurred_at, to_json(payload), idempotency_key),
            )
            return cursor.rowcount > 0

    def list_order_events_for_trade_date(self, trade_date: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM order_events
                    WHERE idempotency_key LIKE %s
                    ORDER BY id
                    """,
                    (f"{trade_date}:%",),
                ).fetchall()
            )

    def list_paper_filled_order_events(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM order_events
                    WHERE status = 'PAPER_FILLED'
                    ORDER BY occurred_at, id
                    """
                ).fetchall()
            )

    def list_submitted_order_events_for_trade_date(self, trade_date: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM order_events
                    WHERE idempotency_key LIKE %s
                      AND status = 'SUBMITTED'
                    ORDER BY id
                    """,
                    (f"{trade_date}:%",),
                ).fetchall()
            )

    def list_order_statuses(self, trade_date: str) -> list[StoredOrderStatus]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    oc.id,
                    oc.trade_date,
                    oc.symbol,
                    oc.action,
                    oc.quantity,
                    oc.reason,
                    oc.idempotency_key,
                    (
                        SELECT a.status
                        FROM approvals a
                        WHERE a.order_candidate_id = oc.id
                          AND a.stage = 'strategy'
                        ORDER BY a.id DESC
                        LIMIT 1
                    ) AS approval_status,
                    (
                        SELECT a.status
                        FROM approvals a
                        WHERE a.order_candidate_id = oc.id
                          AND a.stage = 'final'
                        ORDER BY a.id DESC
                        LIMIT 1
                    ) AS final_approval_status,
                    (
                        SELECT oe.status
                        FROM order_events oe
                        WHERE oe.idempotency_key = oc.idempotency_key
                        ORDER BY oe.id DESC
                        LIMIT 1
                    ) AS event_status
                FROM order_candidates oc
                WHERE oc.trade_date = %s
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return [
            StoredOrderStatus(
                id=int(row["id"]),
                trade_date=str(row["trade_date"]),
                symbol=str(row["symbol"]),
                action=str(row["action"]),
                quantity=int(row["quantity"]),
                reason=str(row["reason"]),
                idempotency_key=str(row["idempotency_key"]),
                approval_status=row["approval_status"],
                final_approval_status=row["final_approval_status"],
                event_status=row["event_status"],
            )
            for row in rows
        ]

    def list_rollover_order_candidates(self, trade_date: str) -> list[StoredOrderCandidate]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT oc.*
                FROM order_candidates oc
                WHERE oc.trade_date = %s
                  AND (
                    NOT EXISTS (
                        SELECT 1
                        FROM order_events oe
                        WHERE oe.idempotency_key = oc.idempotency_key
                    )
                    OR (
                        SELECT oe.status
                        FROM order_events oe
                        WHERE oe.idempotency_key = oc.idempotency_key
                        ORDER BY oe.id DESC
                        LIMIT 1
                    ) IN ('FAILED', 'REJECTED', 'BLOCKED', 'PENDING')
                  )
                ORDER BY oc.id
                """,
                (trade_date,),
            ).fetchall()
        return rows_to_candidates(rows)

    def record_report(
        self,
        report_date: str,
        report_type: str,
        payload: dict[str, Any],
        sent_at: Optional[str] = None,
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO reports(report_date, report_type, payload_json, sent_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (report_date, report_type, to_json(payload), sent_at),
            ).fetchone()
            return int(row["id"])

    def get_state(self, key: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = %s",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value),
            )

    def count_table(self, table: str) -> int:
        with self.connect() as connection:
            row = connection.execute(f"SELECT count(*) AS count FROM {table}").fetchone()
            return int(row["count"])


def to_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def rows_to_candidates(rows: list[Any]) -> list[StoredOrderCandidate]:
    return [
        StoredOrderCandidate(
            id=int(row["id"]),
            trade_date=str(row["trade_date"]),
            symbol=str(row["symbol"]),
            action=str(row["action"]),
            quantity=int(row["quantity"]),
            reason=str(row["reason"]),
            idempotency_key=str(row["idempotency_key"]),
            payload=json.loads(row["payload_json"]),
        )
        for row in rows
    ]


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    total_equity REAL NOT NULL,
    cash REAL NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    market_cap REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(universe_date, symbol)
);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL,
    units INTEGER NOT NULL,
    last_entry_price REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_candidate_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'strategy',
    status TEXT NOT NULL,
    responded_at TEXT NOT NULL,
    response_text TEXT NOT NULL,
    FOREIGN KEY(order_candidate_id) REFERENCES order_candidates(id)
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_candidate_id INTEGER,
    broker_order_id TEXT,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT,
    FOREIGN KEY(order_candidate_id) REFERENCES order_candidates(id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    report_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    captured_at TEXT NOT NULL,
    total_equity DOUBLE PRECISION NOT NULL,
    cash DOUBLE PRECISION NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_members (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    universe_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    market_cap DOUBLE PRECISION,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(universe_date, symbol)
);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL,
    units INTEGER NOT NULL,
    last_entry_price DOUBLE PRECISION,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_candidates (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_candidate_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'strategy',
    status TEXT NOT NULL,
    responded_at TEXT NOT NULL,
    response_text TEXT NOT NULL,
    FOREIGN KEY(order_candidate_id) REFERENCES order_candidates(id)
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_candidate_id INTEGER,
    broker_order_id TEXT,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT,
    FOREIGN KEY(order_candidate_id) REFERENCES order_candidates(id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    report_date TEXT NOT NULL,
    report_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
