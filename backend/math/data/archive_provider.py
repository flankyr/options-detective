"""Local parquet archive (archive.py) as a point-in-time provider."""

from __future__ import annotations

from datetime import date

import archive

from .base import Provider, Unsupported
from .schema import Capabilities, finalize, make_contract


class ArchiveProvider(Provider):
    name = "archive"
    capabilities = Capabilities(
        live=False, historical=True, delayed_minutes=-1, has_greeks=False,
        has_open_interest=True, has_volume=True, history_depth_days=0,
        license_note="Your own snapshots; derived from whichever live source took them.",
    )

    def __init__(self, root=None):
        self.root = root or archive.default_root()

    def available_dates(self, symbol):
        return archive.list_dates(self.root, symbol)

    def chain(self, symbol, asof=None, *, max_expirations=6, min_dte=1, max_dte=120):
        if asof is None:
            raise Unsupported("archive serves past dates only")
        df = archive.read_snapshot(self.root, symbol, asof)
        if df is None or len(df) == 0:
            raise Unsupported(f"no archived chain for {symbol.upper()} on {asof.isoformat()}")
        return payload_from_rows(df.to_dict("records"), symbol, asof, self.name,
                                 self.capabilities, max_expirations, min_dte, max_dte)


def payload_from_rows(rows, symbol, asof, provider, capabilities,
                      max_expirations=6, min_dte=1, max_dte=120):
    """Flat archive rows (archive.COLUMNS) -> schema v2 payload."""
    if not rows:
        raise Unsupported("empty snapshot")
    chains = {}
    for r in rows:
        exp = r["expiration"]
        dte = (date.fromisoformat(exp) - asof).days
        if dte < min_dte or dte > max_dte:
            continue
        c = make_contract(r["strike"], r.get("bid"), r.get("ask"), r.get("mid"),
                          r.get("volume"), r.get("openInterest"), r.get("impliedVolatility"),
                          r.get("lastTradeAt"))
        if c is None:
            continue
        side = "calls" if r["type"] == "call" else "puts"
        chains.setdefault(exp, {"calls": [], "puts": []})[side].append(c)
    exps = sorted(chains)
    if len(exps) > max_expirations:
        # keep nearest and furthest, spread the rest evenly (same rule as market_data)
        step = (len(exps) - 1) / (max_expirations - 1)
        keep = sorted({round(i * step) for i in range(max_expirations)})
        exps = [exps[i] for i in keep]
        chains = {e: chains[e] for e in exps}
    first = rows[0]
    payload = {
        "symbol": symbol.upper(),
        "price": float(first["underlyingClose"]),
        "underlyingClose": float(first["underlyingClose"]),
        "atmIv": first.get("atmIv"),
        "chains": chains,
        "lastTradeAt": max((r["lastTradeAt"] for r in rows if r.get("lastTradeAt")), default=None),
        "fetchedAt": first.get("fetchedAt"),
    }
    return finalize(payload, provider=provider, asof=asof.isoformat(), capabilities=capabilities)
