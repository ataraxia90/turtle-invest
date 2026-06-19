from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.rehearsal import run_local_rehearsal
from turtle_invest.storage import SQLiteStore


class RehearsalTests(unittest.TestCase):
    def test_run_local_rehearsal_exercises_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_local_rehearsal(SQLiteStore(Path(tmp) / "state.db"), "2099-01-01")

            self.assertTrue(result.candidate_created)
            self.assertEqual(result.approvals_recorded, 1)
            self.assertEqual(result.first_execution_count, 1)
            self.assertEqual(result.second_execution_count, 0)


if __name__ == "__main__":
    unittest.main()
