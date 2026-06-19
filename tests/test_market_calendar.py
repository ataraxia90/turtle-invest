from __future__ import annotations

import unittest

from datetime import date, datetime, timedelta, timezone

from turtle_invest.market_calendar import (
    default_us_trade_date,
    good_friday,
    is_after_regular_market_open,
    is_trading_day,
    market_open_datetime,
    next_trading_day,
    nyse_holidays,
)


class MarketCalendarTests(unittest.TestCase):
    def test_is_trading_day_weekday_and_holiday(self) -> None:
        self.assertTrue(is_trading_day("2026-06-12"))
        self.assertFalse(is_trading_day("2026-06-13"))
        self.assertFalse(is_trading_day("2026-07-03"))
        self.assertFalse(is_trading_day("2026-12-25"))

    def test_good_friday(self) -> None:
        self.assertEqual(good_friday(2026), date(2026, 4, 3))

    def test_nyse_holidays_contains_major_dates(self) -> None:
        holidays = nyse_holidays(2026)

        self.assertIn(date(2026, 1, 1), holidays)
        self.assertIn(date(2026, 6, 19), holidays)
        self.assertIn(date(2026, 11, 26), holidays)

    def test_next_trading_day_skips_weekends_and_holidays(self) -> None:
        self.assertEqual(next_trading_day("2026-07-02"), date(2026, 7, 6))

    def test_default_us_trade_date_shape(self) -> None:
        value = default_us_trade_date()

        self.assertEqual(len(value), 10)
        self.assertEqual(value[4], "-")
        self.assertEqual(value[7], "-")

    def test_market_open_datetime_uses_us_dst(self) -> None:
        summer = market_open_datetime("2026-06-15", buffer_minutes=5)
        winter = market_open_datetime("2026-12-15", buffer_minutes=5)
        seoul = timezone(timedelta(hours=9))

        self.assertEqual(summer.astimezone(seoul).hour, 22)
        self.assertEqual(summer.astimezone(seoul).minute, 35)
        self.assertEqual(winter.astimezone(seoul).hour, 23)
        self.assertEqual(winter.astimezone(seoul).minute, 35)

    def test_is_after_regular_market_open(self) -> None:
        eastern = market_open_datetime("2026-06-15").tzinfo

        self.assertFalse(
            is_after_regular_market_open(
                datetime(2026, 6, 15, 9, 34, tzinfo=eastern),
                buffer_minutes=5,
            )
        )
        self.assertTrue(
            is_after_regular_market_open(
                datetime(2026, 6, 15, 9, 35, tzinfo=eastern),
                buffer_minutes=5,
            )
        )
        self.assertFalse(
            is_after_regular_market_open(
                datetime(2026, 6, 14, 10, 0, tzinfo=eastern),
                buffer_minutes=5,
            )
        )


if __name__ == "__main__":
    unittest.main()
