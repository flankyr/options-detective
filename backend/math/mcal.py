"""NYSE trading calendar: holidays, trading-day counts, close times (A16)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

# Full-day NYSE closures 2024-2028 (observed dates). Half days are still
# trading days for DTE purposes.
HOLIDAYS = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29), date(2024, 5, 27),
    date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28), date(2024, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
    # 2028
    date(2028, 1, 17), date(2028, 2, 21), date(2028, 4, 14), date(2028, 5, 29), date(2028, 6, 19),
    date(2028, 7, 4), date(2028, 9, 4), date(2028, 11, 23), date(2028, 12, 25),
}

# Options on broad indices settle off the 16:15 ET close of the index session
INDEX_CLOSE = {"SPX", "SPXW", "XSP", "NDX", "NDXP", "RUT", "RUTW", "VIX", "DJX", "OEX"}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def trading_days_between(start: date, end: date) -> int:
    """Trading days in (start, end]: the number of sessions left to trade."""
    if end <= start:
        return 0
    n = 0
    d = start + timedelta(days=1)
    while d <= end:
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def next_trading_day(d: date) -> date:
    d = d + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def close_time(symbol: str) -> time:
    return time(16, 15) if str(symbol).upper() in INDEX_CLOSE else time(16, 0)


def expiration_ok(exp: date) -> bool:
    """Listed expirations fall on trading days; a holiday Friday rolls to Thursday."""
    return is_trading_day(exp)


def year_fraction(asof: date, exp: date, symbol: str = "", now: datetime | None = None) -> float:
    """Calendar-day T in years (the pricing convention), with the intraday
    fraction of the current session when `now` is given."""
    days = (exp - asof).days
    if now is not None:
        ct = close_time(symbol)
        remaining = (ct.hour * 60 + ct.minute) - (now.hour * 60 + now.minute)
        days += max(min(remaining / (24 * 60), 1.0), 0.0) - 1.0 if days > 0 else 0.0
    return max(days, 0.0) / 365.0
