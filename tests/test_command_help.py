from __future__ import annotations

import unittest

from turtle_invest.command_help import format_commands


class CommandHelpTests(unittest.TestCase):
    def test_format_commands_contains_operational_commands(self) -> None:
        text = format_commands()

        self.assertIn("pre-market", text)
        self.assertIn("market-close", text)
        self.assertIn("tax-harvest-report", text)
        self.assertIn("backup-db", text)


if __name__ == "__main__":
    unittest.main()
