from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from turtle_invest.backup import backup_file


class BackupTests(unittest.TestCase):
    def test_backup_file_copies_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "state.db"
            source.write_text("hello", encoding="utf-8")

            result = backup_file(str(source), str(Path(tmp) / "backups"))

            self.assertTrue(result.copied)
            self.assertTrue(result.destination.exists())
            self.assertEqual(result.destination.read_text(encoding="utf-8"), "hello")

    def test_backup_file_reports_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = backup_file(str(Path(tmp) / "missing.db"), str(Path(tmp) / "backups"))

            self.assertFalse(result.copied)


if __name__ == "__main__":
    unittest.main()
