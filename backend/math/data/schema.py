"""Normalized chain payload (schema v2) shared by every provider.

Top level (superset of the v1 market_data.fetch() shape, so the Node data
layer keeps working):
    symbol, price, atmIv, ivRank, ivRankMethod, expirations, chains,
    lastTradeAt, fetchedAt
  + asof            ISO date the chain describes (trading session)
  + provider        which source produced it
  + capabilities    what the source can and cannot supply (see Capabilities)
  + underlyingClose close of the underlying for `asof` (== price for live)
  + dividendYield   trailing-12m cash dividends / price, or None
  + rates           {expiration: annualized continuously-compounded r} or {}
  + liquidityKnown  False when volume/OI are not available from this source

Per contract: strike, bid, ask, mid, volume, openInterest, impliedVolatility
(vendor), spreadPct, timestamp  + ivVendor (same as impliedVolatility, kept
explicit for Phase 2, which adds ivModel) + optional greeks dict when the
source publishes them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capabilities:
    live: bool                 # can serve asof=None
    historical: bool           # can serve a past asof
    delayed_minutes: int       # 0 = real-time, 15 = delayed, -1 = end of day
    has_greeks: bool
    has_open_interest: bool
    has_volume: bool
    history_depth_days: int    # 0 = none / unknown
    license_note: str          # shown in the UI; redistribution caveats live here

    def to_dict(self) -> dict:
        return asdict(self)


def clean(value):
    """float(value) or None for missing/NaN/inf."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def make_contract(strike, bid=None, ask=None, last=None, volume=None, open_interest=None,
                  iv=None, timestamp=None, greeks=None) -> dict | None:
    """Build one normalized contract. Returns None for an unusable strike."""
    strike = clean(strike)
    if strike is None or strike <= 0.0:
        return None
    bid = clean(bid) or 0.0
    ask = clean(ask) or 0.0
    last = clean(last) or 0.0
    if bid > 0.0 and ask >= bid:
        mid = round((bid + ask) / 2.0, 4)
        spread_pct = round((ask - bid) / mid, 4) if mid > 0 else None
    else:
        mid = round(last, 4) if last > 0.0 else None
        spread_pct = None
    iv = clean(iv)
    if iv is not None and iv <= 0.0:
        iv = None
    vol = clean(volume)
    oi = clean(open_interest)
    out = {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "volume": int(vol) if vol is not None else None,
        "openInterest": int(oi) if oi is not None else None,
        "impliedVolatility": iv,
        "ivVendor": iv,
        "spreadPct": spread_pct,
        "timestamp": timestamp,
    }
    if greeks:
        g = {k: clean(v) for k, v in greeks.items()}
        if any(v is not None for v in g.values()):
            out["greeks"] = g
    return out


def atm_iv(chain: dict, spot: float):
    """Average call/put vendor IV at the strike nearest spot; None if absent."""
    found = []
    for side in ("calls", "puts"):
        with_iv = [c for c in chain.get(side, []) if c.get("impliedVolatility")]
        if with_iv:
            nearest = min(with_iv, key=lambda c: abs(c["strike"] - spot))
            found.append(nearest["impliedVolatility"])
    return sum(found) / len(found) if found else None


def finalize(payload: dict, *, provider: str, asof: str, capabilities: Capabilities,
             dividend_yield=None, rates=None) -> dict:
    """Stamp the v2 fields onto a v1-shaped payload and sort chains by strike."""
    for chain in payload.get("chains", {}).values():
        for side in ("calls", "puts"):
            chain[side] = sorted(chain.get(side, []), key=lambda c: c["strike"])
    payload["expirations"] = sorted(payload.get("chains", {}).keys())
    payload["asof"] = asof
    payload["provider"] = provider
    payload["capabilities"] = capabilities.to_dict()
    payload["underlyingClose"] = payload.get("underlyingClose", payload.get("price"))
    payload["dividendYield"] = dividend_yield
    payload["rates"] = rates or {}
    payload["liquidityKnown"] = capabilities.has_volume and capabilities.has_open_interest
    if payload.get("atmIv") is None and payload["expirations"]:
        payload["atmIv"] = atm_iv(payload["chains"][payload["expirations"][0]], payload["price"])
    payload.setdefault("ivRank", None)
    payload.setdefault("ivRankMethod", None)
    payload.setdefault("lastTradeAt", None)
    return payload


REQUIRED_TOP = ("symbol", "price", "expirations", "chains", "fetchedAt", "asof", "provider")


def validate(payload: dict) -> list:
    """Return a list of problems (empty = valid). Cheap structural checks only."""
    problems = []
    for key in REQUIRED_TOP:
        if key not in payload:
            problems.append(f"missing {key}")
    if problems:
        return problems
    if not (isinstance(payload["price"], (int, float)) and payload["price"] > 0):
        problems.append("price must be > 0")
    for exp in payload["expirations"]:
        chain = payload["chains"].get(exp)
        if not chain:
            problems.append(f"expiration {exp} has no chain")
            continue
        for side in ("calls", "puts"):
            strikes = [c["strike"] for c in chain.get(side, [])]
            if strikes != sorted(strikes):
                problems.append(f"{exp} {side} not strike-sorted")
    return problems
