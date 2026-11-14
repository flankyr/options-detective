"""Daily option-chain archive: point-in-time snapshots for backtesting.

Why this exists
---------------
Every backtest, calibration plot and implied-volatility history needs to know
what the option chain looked like ON a given day. Free sources hand you today's
chain and little or nothing behind it, so the cheapest history is the one we
record ourselves. The clock starts the first day this runs.

Layout
------
    <root>/<SYMBOL>/<YYYY-MM-DD>.parquet      one file per symbol per trading day
    <root>/<SYMBOL>/meta.json                 last snapshot summary (quick status)

Each parquet file holds one row per contract with the columns in COLUMNS, plus
the underlying close, the ATM IV proxy and the fetch timestamp repeated on
every row (denormalised on purpose: a single file is self-describing).

A snapshot is keyed by the *trading date* it represents, not the wall-clock
day it was taken: a fetch at 23:30 ET on Monday still describes Monday's close
and a Saturday fetch describes Friday. Re-running on the same key overwrites,
so the job is idempotent.

CLI (mirrors the other od-math entry points):
    stdin : {"symbols": ["SPY", ...], "root": "<dir>", "force": false,
             "max_expirations": 12}
    stdout: {"ok": true, "result": {"date": ..., "written": [...], "skipped": [...],
                                    "errors": {...}}}

Everything that touches the network goes through the `fetch` callable so the
module is unit-testable with a fake fetcher.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)

DEFAULT_WATCHLIST = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
DEFAULT_MAX_EXPIRATIONS = 12

COLUMNS = [
    "symbol", "asof", "expiration", "type", "strike",
    "bid", "ask", "mid", "volume", "openInterest", "impliedVolatility",
    "spreadPct", "lastTradeAt", "underlyingClose", "atmIv", "fetchedAt",
]


def default_root() -> str:
    """backend/data/archive next to this file unless OD_ARCHIVE_DIR says otherwise."""
    env = os.environ.get("OD_ARCHIVE_DIR")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "archive")


def trading_date_for(now: datetime | None = None) -> date:
    """The trading session a snapshot taken at `now` describes.

    Before the 16:00 ET close the chain still describes the previous session's
    close (intraday marks are not a closing snapshot), so we key by the prior
    weekday. Weekends roll back to Friday. Exchange holidays are handled in
    Phase 4 with a calendar table; until then a holiday simply re-writes the
    previous day's file with identical data, which is harmless.
    """
    now = now.astimezone(ET) if now else datetime.now(ET)
    d = now.date()
    if now.time() < MARKET_CLOSE:
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        d -= timedelta(days=1)
    return d


def flatten(payload: dict, asof: date) -> list:
    """market_data.fetch() payload -> list of row dicts (one per contract)."""
    rows = []
    sym = payload["symbol"]
    common = {
        "symbol": sym,
        "asof": asof.isoformat(),
        "underlyingClose": float(payload["price"]),
        "atmIv": payload.get("atmIv"),
        "fetchedAt": payload.get("fetchedAt"),
    }
    for exp, chain in payload.get("chains", {}).items():
        for side, kind in (("calls", "call"), ("puts", "put")):
            for c in chain.get(side, []):
                rows.append({
                    **common,
                    "expiration": exp,
                    "type": kind,
                    "strike": float(c["strike"]),
                    "bid": c.get("bid"),
                    "ask": c.get("ask"),
                    "mid": c.get("mid"),
                    "volume": c.get("volume"),
                    "openInterest": c.get("openInterest"),
                    "impliedVolatility": c.get("impliedVolatility"),
                    "spreadPct": c.get("spreadPct"),
                    "lastTradeAt": c.get("timestamp"),
                })
    return rows


def snapshot_path(root: str, symbol: str, asof: date) -> str:
    return os.path.join(root, symbol.upper(), f"{asof.isoformat()}.parquet")


def write_snapshot(rows: list, path: str) -> int:
    """Rows -> parquet at `path`. Returns row count. Creates parent dirs."""
    import pandas as pd  # lazy: keeps trading_date_for / flatten importable without pandas

    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame(rows, columns=COLUMNS)
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)  # atomic on the same filesystem: no half-written files
    return len(df)


def read_snapshot(root: str, symbol: str, asof: date | str):
    """Parquet -> DataFrame, or None when that day was not archived."""
    import pandas as pd

    if isinstance(asof, str):
        asof = date.fromisoformat(asof)
    path = snapshot_path(root, symbol, asof)
    if not os.path.exists(path):
        return None
    return pd.read_parquet(path)


def list_dates(root: str, symbol: str) -> list:
    """Sorted ISO dates archived for `symbol`."""
    d = os.path.join(root, symbol.upper())
    if not os.path.isdir(d):
        return []
    return sorted(f[:-8] for f in os.listdir(d) if f.endswith(".parquet"))


def status(root: str, symbols=None) -> dict:
    """Per-symbol archive summary for the UI."""
    out = {}
    syms = symbols or (sorted(os.listdir(root)) if os.path.isdir(root) else [])
    for s in syms:
        dates = list_dates(root, s)
        size = 0
        d = os.path.join(root, s.upper())
        if os.path.isdir(d):
            size = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
        out[s.upper()] = {
            "days": len(dates),
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "bytes": size,
        }
    return out


def run(symbols, root, fetch, *, force=False, now=None,
        max_expirations=DEFAULT_MAX_EXPIRATIONS) -> dict:
    """Snapshot each symbol for today's trading date. Errors are isolated per symbol."""
    asof = trading_date_for(now)
    written, skipped, errors = [], [], {}
    for raw in symbols:
        sym = str(raw).strip().upper()
        if not sym:
            continue
        path = snapshot_path(root, sym, asof)
        if os.path.exists(path) and not force:
            skipped.append(sym)
            continue
        try:
            payload = fetch(sym, max_expirations=max_expirations, min_dte=0, max_dte=400)
            n = write_snapshot(flatten(payload, asof), path)
            written.append({"symbol": sym, "rows": n})
            with open(os.path.join(root, sym, "meta.json"), "w", encoding="utf-8") as fh:
                json.dump({"last": asof.isoformat(), "rows": n,
                           "fetchedAt": payload.get("fetchedAt")}, fh)
        except Exception as exc:  # one bad symbol must not stop the others
            errors[sym] = f"{type(exc).__name__}: {exc}"
    return {"date": asof.isoformat(), "root": os.path.abspath(root),
            "written": written, "skipped": skipped, "errors": errors}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        response = {"ok": False, "error": f"invalid JSON request: {exc}"}
    else:
        if not isinstance(req, dict):
            response = {"ok": False, "error": "request must be a JSON object"}
        else:
            root = req.get("root") or default_root()
            if req.get("status"):
                response = {"ok": True, "result": {"root": os.path.abspath(root),
                                                    "symbols": status(root, req.get("symbols"))}}
            else:
                from market_data import fetch  # network import kept out of the pure path
                symbols = req.get("symbols") or DEFAULT_WATCHLIST
                try:
                    result = run(symbols, root, fetch, force=bool(req.get("force")),
                                 max_expirations=int(req.get("max_expirations",
                                                             DEFAULT_MAX_EXPIRATIONS)))
                    response = {"ok": True, "result": result}
                except Exception as exc:
                    response = {"ok": False, "error": f"archive failed: {type(exc).__name__}: {exc}"}
    json.dump(response, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
