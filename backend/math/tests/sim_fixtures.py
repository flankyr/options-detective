"""Synthetic archive for simulator tests: GBM closes, BSM chains with skew,
monthly expirations, written as parquet snapshots the archive provider reads."""
import math
from datetime import date, timedelta

import numpy as np

import archive
import bsm
import mcal
from data.archive_provider import ArchiveProvider
from data.rates import RateCurve
from data.service import DataService


def third_friday(y, m):
    d = date(y, m, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def build_archive(root, *, symbol="TEST", start=date(2026, 1, 5), days=140, spot0=100.0,
                  sigma_real=0.18, iv_atm=0.22, skew=-0.15, seed=0, drift=0.0):
    """Realized vol below implied by default: the VRP exists in this world."""
    rng = np.random.default_rng(seed)
    d = start
    spot = spot0
    dates = []
    while len(dates) < days:
        if mcal.is_trading_day(d):
            dates.append(d)
        d += timedelta(days=1)
    for i, asof in enumerate(dates):
        if i:
            spot *= math.exp((drift - 0.5 * sigma_real ** 2) / 252 + sigma_real / math.sqrt(252) * rng.normal())
        exps = []
        y, m = asof.year, asof.month
        for k in range(4):
            mm = m + k
            yy = y + (mm - 1) // 12
            mm = (mm - 1) % 12 + 1
            e = third_friday(yy, mm)
            if (e - asof).days >= 7:
                exps.append(e)
        exps = exps[:3]
        chains = {}
        for e in exps:
            T = (e - asof).days / 365
            F = spot * math.exp((0.04 - 0.01) * T)
            calls, puts = [], []
            for K in np.arange(round(spot * 0.7 / 2.5) * 2.5, spot * 1.3, 2.5):
                k = math.log(K / F)
                iv = max(0.06, iv_atm + skew * k + 0.3 * k * k)
                for ic, lst in ((True, calls), (False, puts)):
                    mid = max(float(bsm.price(spot, K, T, 0.04, 0.01, iv, ic)), 0.01)
                    half = max(0.03, mid * 0.01)
                    lst.append({"strike": float(K), "bid": round(mid - half, 2), "ask": round(mid + half, 2),
                                "mid": round(mid, 4), "volume": 600, "openInterest": 3000,
                                "impliedVolatility": iv, "spreadPct": round(2 * half / mid, 4),
                                "timestamp": f"{asof.isoformat()}T20:59:00+00:00"})
            chains[e.isoformat()] = {"calls": calls, "puts": puts}
        payload = {"symbol": symbol, "price": round(spot, 4), "atmIv": iv_atm, "fetchedAt": f"{asof.isoformat()}T21:30:00+00:00",
                   "expirations": [e.isoformat() for e in exps], "chains": chains}
        archive.write_snapshot(archive.flatten(payload, asof), archive.snapshot_path(root, symbol, asof))
    return [x.isoformat() for x in dates]


CSV = """Date,"1 Mo","2 Mo","3 Mo","6 Mo","1 Yr","2 Yr","5 Yr","10 Yr","30 Yr"
01/02/2026,4.00,4.00,4.00,4.00,4.00,4.00,4.00,4.00,4.00
"""


def service_for(root):
    import os
    arch = ArchiveProvider(os.path.join(root, "archive"))
    rc = RateCurve(root, fetch=lambda y: CSV)
    return DataService(root=root, providers=[arch], rates=rc, config={}, today=lambda: date(2030, 1, 1))
