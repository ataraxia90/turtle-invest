from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.portfolio_sync import parse_number, parse_overseas_positions, sync_overseas_balance
from turtle_invest.storage import SQLiteStore


class FakeKISClient:
    def get_overseas_balance(self) -> dict:
        return {
            "rt_cd": "0",
            "msg1": "ok",
            "output1": [
                {
                    "ovrs_pdno": "AAPL",
                    "ovrs_cblc_qty": "3",
                    "pchs_avg_pric": "200.50",
                    "ovrs_stck_evlu_amt": "630.00",
                },
                {"ovrs_pdno": "MSFT", "ovrs_cblc_qty": "0"},
            ],
            "output2": {"tot_evlu_pfls_amt": "630.00", "frcr_buy_amt_smtl1": "100.00"},
        }


class PortfolioSyncTests(unittest.TestCase):
    def test_parse_number_handles_commas_and_blanks(self) -> None:
        self.assertEqual(parse_number("1,234.50"), 1234.50)
        self.assertIsNone(parse_number(""))
        self.assertIsNone(parse_number("abc"))

    def test_parse_overseas_positions_skips_zero_quantity(self) -> None:
        positions = parse_overseas_positions(FakeKISClient().get_overseas_balance())

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "AAPL")
        self.assertEqual(positions[0].quantity, 3)
        self.assertEqual(positions[0].average_price, 200.50)

    def test_sync_overseas_balance_records_snapshot_and_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")

            result = sync_overseas_balance(FakeKISClient(), store)  # type: ignore[arg-type]
            position = store.get_position("AAPL")

            self.assertEqual(result.positions_count, 1)
            self.assertEqual(result.total_equity, 630.0)
            self.assertIsNotNone(position)
            self.assertEqual(position["quantity"], 3)
            self.assertEqual(position["units"], 1)

    def test_sync_preserves_existing_units_and_marks_missing_positions_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "state.db")
            store.initialize()
            store.upsert_position("AAPL", 2, 2, 190.0, "2026-06-10T00:00:00Z")
            store.upsert_position("MSFT", 1, 1, 300.0, "2026-06-10T00:00:00Z")

            sync_overseas_balance(FakeKISClient(), store)  # type: ignore[arg-type]
            aapl = store.get_position("AAPL")
            msft = store.get_position("MSFT")

            self.assertEqual(aapl["quantity"], 3)
            self.assertEqual(aapl["units"], 2)
            self.assertEqual(aapl["last_entry_price"], 190.0)
            self.assertEqual(msft["quantity"], 0)
            self.assertEqual(msft["units"], 0)


if __name__ == "__main__":
    unittest.main()
