from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from turtle_invest.broker.kis import KISClient
from turtle_invest.storage import SQLiteStore


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: int
    average_price: Optional[float]
    market_value: Optional[float]
    raw: dict[str, Any]


@dataclass(frozen=True)
class BalanceSyncResult:
    captured_at: str
    positions_count: int
    snapshot_id: int
    total_equity: float
    cash: float
    message: str


def sync_overseas_balance(client: KISClient, store: SQLiteStore) -> BalanceSyncResult:
    store.initialize()
    captured_at = datetime.now(timezone.utc).isoformat()
    response = client.get_overseas_balance()
    positions = parse_overseas_positions(response)
    summary = response.get("output2") if isinstance(response.get("output2"), dict) else {}

    total_equity = first_float(
        summary,
        [
            "tot_evlu_pfls_amt",
            "ovrs_tot_pfls",
            "frcr_pchs_amt1",
            "frcr_buy_amt_smtl1",
        ],
    )
    cash = first_float(summary, ["frcr_dncl_amt_2", "frcr_buy_amt_smtl1"], default=0.0)
    snapshot_id = store.record_account_snapshot(
        captured_at=captured_at,
        total_equity=total_equity,
        cash=cash,
        payload=response,
    )

    for position in positions:
        existing = store.get_position(position.symbol)
        existing_units = int(existing["units"]) if existing is not None else 0
        existing_last_entry = existing["last_entry_price"] if existing is not None else None
        store.upsert_position(
            symbol=position.symbol,
            quantity=position.quantity,
            units=existing_units if existing_units > 0 else 1,
            last_entry_price=existing_last_entry if existing_last_entry is not None else position.average_price,
            updated_at=captured_at,
        )

    synced_symbols = {position.symbol for position in positions}
    for row in store.list_positions():
        symbol = str(row["symbol"])
        if symbol not in synced_symbols and int(row["quantity"]) > 0:
            store.upsert_position(
                symbol=symbol,
                quantity=0,
                units=0,
                last_entry_price=row["last_entry_price"],
                updated_at=captured_at,
            )

    return BalanceSyncResult(
        captured_at=captured_at,
        positions_count=len(positions),
        snapshot_id=snapshot_id,
        total_equity=total_equity,
        cash=cash,
        message=str(response.get("msg1", "")),
    )


def parse_overseas_positions(response: dict[str, Any]) -> list[BrokerPosition]:
    output = response.get("output1") or []
    if isinstance(output, dict):
        rows = [output]
    elif isinstance(output, list):
        rows = [row for row in output if isinstance(row, dict)]
    else:
        rows = []

    positions: list[BrokerPosition] = []
    for row in rows:
        symbol = first_text(row, ["ovrs_pdno", "pdno", "symb", "symbol"])
        quantity = int(first_float(row, ["ovrs_cblc_qty", "cblc_qty", "ord_psbl_qty", "quantity"]))
        if not symbol or quantity <= 0:
            continue
        positions.append(
            BrokerPosition(
                symbol=symbol,
                quantity=quantity,
                average_price=optional_float(row, ["pchs_avg_pric", "avg_unpr", "average_price"]),
                market_value=optional_float(row, ["ovrs_stck_evlu_amt", "evlu_amt", "market_value"]),
                raw=row,
            )
        )
    return positions


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def first_float(row: dict[str, Any], keys: list[str], default: float = 0.0) -> float:
    value = optional_float(row, keys)
    return default if value is None else value


def optional_float(row: dict[str, Any], keys: list[str]) -> Optional[float]:
    for key in keys:
        value = row.get(key)
        parsed = parse_number(value)
        if parsed is not None:
            return parsed
    return None


def parse_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
