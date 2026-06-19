from __future__ import annotations

import unittest

from turtle_invest.market_data import parse_daily_candles, parse_number


class MarketDataTests(unittest.TestCase):
    def test_parse_number(self) -> None:
        self.assertEqual(parse_number("1,234.56"), 1234.56)
        self.assertIsNone(parse_number(""))
        self.assertIsNone(parse_number("bad"))

    def test_parse_daily_candles_sorts_by_date(self) -> None:
        response = {
            "output2": [
                {
                    "xymd": "20260103",
                    "open": "103.00",
                    "high": "105.00",
                    "low": "101.00",
                    "clos": "104.00",
                    "tvol": "1000",
                },
                {
                    "xymd": "20260102",
                    "open": "100.00",
                    "high": "102.00",
                    "low": "99.00",
                    "clos": "101.00",
                    "tvol": "900",
                },
            ]
        }

        candles = parse_daily_candles(response)

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].date, "20260102")
        self.assertEqual(candles[1].close, 104.0)


if __name__ == "__main__":
    unittest.main()
