import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import archive

ET = ZoneInfo("America/New_York")


def fake_payload(symbol="SPY", price=500.0):
    return {
        "symbol": symbol,
        "price": price,
        "atmIv": 0.18,
        "fetchedAt": "2026-11-13T21:30:00+00:00",
        "expirations": ["2026-11-20", "2026-12-18"],
        "chains": {
            "2026-11-20": {
                "calls": [{"strike": 500, "bid": 5.0, "ask": 5.2, "mid": 5.1, "volume": 1000,
                           "openInterest": 5000, "impliedVolatility": 0.17, "spreadPct": 0.039,
                           "timestamp": "2026-11-13T20:59:00+00:00"}],
                "puts": [{"strike": 500, "bid": 4.8, "ask": 5.0, "mid": 4.9, "volume": 900,
                          "openInterest": 4000, "impliedVolatility": 0.19, "spreadPct": 0.04,
                          "timestamp": None}],
            },
            "2026-12-18": {"calls": [], "puts": [
                {"strike": 480, "bid": 6.0, "ask": 6.4, "mid": 6.2, "volume": 10,
                 "openInterest": 20, "impliedVolatility": None, "spreadPct": None, "timestamp": None}]},
        },
    }


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 13, 17, 0, tzinfo=ET), date(2026, 11, 13)),   # Fri after close
    (datetime(2026, 11, 13, 10, 0, tzinfo=ET), date(2026, 11, 12)),   # Fri before close -> Thu
    (datetime(2026, 11, 14, 12, 0, tzinfo=ET), date(2026, 11, 13)),   # Saturday -> Friday
    (datetime(2026, 11, 16, 9, 0, tzinfo=ET), date(2026, 11, 13)),    # Monday pre-open -> Friday
    (datetime(2026, 11, 16, 16, 0, tzinfo=ET), date(2026, 11, 16)),   # Monday at the close
])
def test_trading_date_for(now, expected):
    assert archive.trading_date_for(now) == expected


def test_trading_date_accepts_utc():
    utc = datetime(2026, 11, 14, 2, 0, tzinfo=ZoneInfo("UTC"))  # Fri 21:00 ET
    assert archive.trading_date_for(utc) == date(2026, 11, 13)


def test_flatten_one_row_per_contract():
    rows = archive.flatten(fake_payload(), date(2026, 11, 13))
    assert len(rows) == 3
    assert {r["type"] for r in rows} == {"call", "put"}
    assert all(r["asof"] == "2026-11-13" for r in rows)
    assert all(r["underlyingClose"] == 500.0 for r in rows)
    assert set(rows[0]) == set(archive.COLUMNS)


def test_run_writes_reads_and_is_idempotent(tmp_path):
    calls = []

    def fetch(sym, **kw):
        calls.append(sym)
        return fake_payload(sym)

    now = datetime(2026, 11, 13, 17, 0, tzinfo=ET)
    r1 = archive.run(["spy", "QQQ"], str(tmp_path), fetch, now=now)
    assert r1["date"] == "2026-11-13"
    assert [w["symbol"] for w in r1["written"]] == ["SPY", "QQQ"]
    assert r1["errors"] == {}
    assert calls == ["SPY", "QQQ"]

    df = archive.read_snapshot(str(tmp_path), "SPY", "2026-11-13")
    assert isinstance(df, pd.DataFrame) and len(df) == 3
    assert list(df.columns) == archive.COLUMNS
    assert archive.list_dates(str(tmp_path), "spy") == ["2026-11-13"]

    r2 = archive.run(["SPY", "QQQ"], str(tmp_path), fetch, now=now)
    assert r2["written"] == [] and r2["skipped"] == ["SPY", "QQQ"]
    assert calls == ["SPY", "QQQ"]  # no refetch

    r3 = archive.run(["SPY"], str(tmp_path), fetch, now=now, force=True)
    assert len(r3["written"]) == 1 and calls[-1] == "SPY"


def test_run_isolates_errors(tmp_path):
    def fetch(sym, **kw):
        if sym == "BAD":
            raise ValueError("no options")
        return fake_payload(sym)

    r = archive.run(["BAD", "SPY"], str(tmp_path), fetch,
                    now=datetime(2026, 11, 13, 17, 0, tzinfo=ET))
    assert "BAD" in r["errors"] and "no options" in r["errors"]["BAD"]
    assert [w["symbol"] for w in r["written"]] == ["SPY"]


def test_status_and_missing_snapshot(tmp_path):
    assert archive.read_snapshot(str(tmp_path), "SPY", "2026-01-01") is None
    archive.run(["SPY"], str(tmp_path), lambda s, **k: fake_payload(s),
                now=datetime(2026, 11, 13, 17, 0, tzinfo=ET))
    st = archive.status(str(tmp_path))
    assert st["SPY"]["days"] == 1 and st["SPY"]["last"] == "2026-11-13"
    assert st["SPY"]["bytes"] > 0
    assert json.loads(open(tmp_path / "SPY" / "meta.json").read())["rows"] == 3
