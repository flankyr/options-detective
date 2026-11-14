"""Risk-free rate per expiry from the US Treasury daily par yield curve.

Source: home.treasury.gov daily-treasury-rates CSV (one row per business day,
columns 1 Mo .. 30 Yr in percent). We cache the latest row in <root>/rates.json
with its date, linearly interpolate the par yield to each expiry's tenor and
convert to a continuously-compounded rate: r = ln(1 + y) for the sub-2y part
of the curve that options use (bond-equivalent vs continuous differences are
well under a basis point here).

Fallback when the fetch fails and no cache exists: a flat rate (default 4%)
flagged `source: "fallback"` so the UI can say so.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import urllib.request
from datetime import date, datetime, timezone

TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")

TENORS_YEARS = {
    "1 Mo": 1 / 12, "1.5 Month": 1.5 / 12, "2 Mo": 2 / 12, "3 Mo": 3 / 12, "4 Mo": 4 / 12,
    "6 Mo": 6 / 12, "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0, "5 Yr": 5.0, "7 Yr": 7.0,
    "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0,
}
FALLBACK_RATE = float(os.environ.get("RISK_FREE_RATE", 0.04))


def parse_treasury_csv(text: str) -> dict:
    """Latest row of the Treasury CSV -> {"date": ISO, "curve": {tenor_years: yield_decimal}}."""
    reader = csv.DictReader(io.StringIO(text))
    latest = None
    for row in reader:
        d = row.get("Date")
        if not d:
            continue
        try:
            dt = datetime.strptime(d.strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        if latest is None or dt > latest[0]:
            latest = (dt, row)
    if latest is None:
        raise ValueError("no dated rows in Treasury CSV")
    dt, row = latest
    curve = {}
    for col, years in TENORS_YEARS.items():
        v = row.get(col)
        if v in (None, "", "N/A"):
            continue
        try:
            curve[years] = float(v) / 100.0
        except ValueError:
            continue
    if not curve:
        raise ValueError("Treasury CSV row carries no yields")
    return {"date": dt.isoformat(), "curve": curve}


def interpolate(curve: dict, t_years: float) -> float:
    """Linear interpolation of par yield in tenor; flat beyond the ends."""
    pts = sorted(curve.items())
    if t_years <= pts[0][0]:
        y = pts[0][1]
    elif t_years >= pts[-1][0]:
        y = pts[-1][1]
    else:
        y = None
        for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
            if t0 <= t_years <= t1:
                y = y0 + (y1 - y0) * (t_years - t0) / (t1 - t0)
                break
    return math.log1p(y)  # par (annual) -> continuous


class RateCurve:
    def __init__(self, root, fetch=None, now=None):
        self.cache_file = os.path.join(root, "rates.json")
        self._fetch = fetch or self._fetch_treasury
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.state = self._load()

    @staticmethod
    def _fetch_treasury(year):
        req = urllib.request.Request(TREASURY_CSV.format(year=year),
                                     headers={"User-Agent": "option-obelisk/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")

    def _load(self):
        try:
            with open(self.cache_file, encoding="utf-8") as fh:
                s = json.load(fh)
            s["curve"] = {float(k): v for k, v in s["curve"].items()}
            return s
        except (OSError, ValueError, KeyError):
            return None

    def refresh(self, force=False):
        today = self._now().date()
        if (not force and self.state and self.state.get("fetchedOn") == today.isoformat()):
            return self.state
        try:
            parsed = parse_treasury_csv(self._fetch(today.year))
        except Exception as exc:  # network or format trouble: keep the cache, else fallback
            if self.state:
                self.state["lastError"] = str(exc)
                return self.state
            self.state = {"date": None, "curve": {1.0: FALLBACK_RATE}, "source": "fallback",
                          "fetchedOn": today.isoformat(), "lastError": str(exc)}
            return self.state
        self.state = {**parsed, "source": "treasury.gov", "fetchedOn": today.isoformat()}
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh)
        return self.state

    def rate(self, t_years: float) -> float:
        if not self.state:
            self.refresh()
        return round(interpolate(self.state["curve"], max(t_years, 1 / 365)), 6)

    def rates_for(self, expirations, asof: date) -> dict:
        return {exp: self.rate((date.fromisoformat(exp) - asof).days / 365.0)
                for exp in expirations}

    def describe(self):
        if not self.state:
            self.refresh()
        return {"source": self.state.get("source"), "date": self.state.get("date"),
                "fetchedOn": self.state.get("fetchedOn"), "lastError": self.state.get("lastError"),
                "points": len(self.state.get("curve", {}))}
