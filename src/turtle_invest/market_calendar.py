from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional, Union

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]


MARKET_OPEN_TIME = time(9, 30)


class USEasternTimezone(tzinfo):
    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        return timedelta(hours=-4 if is_us_eastern_dst(dt) else -5)

    def dst(self, dt: Optional[datetime]) -> timedelta:
        return timedelta(hours=1 if is_us_eastern_dst(dt) else 0)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "EDT" if is_us_eastern_dst(dt) else "EST"


def us_eastern_timezone():
    if ZoneInfo is not None:
        try:
            return ZoneInfo("America/New_York")
        except Exception:
            pass
    return USEasternTimezone()


def now_us_eastern() -> datetime:
    return datetime.now(us_eastern_timezone())


def today_us_eastern() -> date:
    return now_us_eastern().date()


def default_us_trade_date() -> str:
    return today_us_eastern().isoformat()


def is_trading_day(value: Union[str, date]) -> bool:
    trading_date = date.fromisoformat(value) if isinstance(value, str) else value
    return trading_date.weekday() < 5 and trading_date not in nyse_holidays(trading_date.year)


def next_trading_day(value: Union[str, date]) -> date:
    current = date.fromisoformat(value) if isinstance(value, str) else value
    current += timedelta(days=1)
    while not is_trading_day(current):
        current += timedelta(days=1)
    return current


def market_open_datetime(value: Union[str, date], buffer_minutes: int = 0) -> datetime:
    trading_date = date.fromisoformat(value) if isinstance(value, str) else value
    return datetime.combine(
        trading_date,
        MARKET_OPEN_TIME,
        us_eastern_timezone(),
    ) + timedelta(minutes=buffer_minutes)


def is_after_regular_market_open(
    value: Optional[datetime] = None,
    buffer_minutes: int = 0,
) -> bool:
    current = value or now_us_eastern()
    if current.tzinfo is None:
        current = current.replace(tzinfo=us_eastern_timezone())
    current = current.astimezone(us_eastern_timezone())
    if not is_trading_day(current.date()):
        return False
    return current >= market_open_datetime(current.date(), buffer_minutes=buffer_minutes)


def is_us_eastern_dst(value: Optional[datetime]) -> bool:
    if value is None:
        return False
    local = value.replace(tzinfo=None)
    year = local.year
    dst_start = datetime.combine(nth_weekday(year, 3, 6, 2), time(2, 0))
    dst_end = datetime.combine(nth_weekday(year, 11, 6, 1), time(2, 0))
    return dst_start <= local < dst_end


def nyse_holidays(year: int) -> set[date]:
    holidays = {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        good_friday(year),
        last_weekday(year, 5, 0),  # Memorial Day
        observed_fixed_holiday(year, 6, 19),  # Juneteenth
        observed_fixed_holiday(year, 7, 4),  # Independence Day
        nth_weekday(year, 9, 0, 1),  # Labor Day
        nth_weekday(year, 11, 3, 4),  # Thanksgiving
        observed_fixed_holiday(year, 12, 25),  # Christmas
    }
    return {holiday for holiday in holidays if holiday.year == year}


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    days_until_weekday = (weekday - current.weekday()) % 7
    return current + timedelta(days=days_until_weekday + (occurrence - 1) * 7)


def last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def good_friday(year: int) -> date:
    return easter_sunday(year) - timedelta(days=2)


def easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
