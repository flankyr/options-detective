"""Option structures and strike selection by delta (Phase 4, replaces the
Node-side %OTM builders in services/candidates.js).

A structure builder takes a per-expiry `Book` (liquid contracts with a smile
vol and a delta) and parameters from the search grid, and returns legs or
None when the chain cannot support it honestly. Every structure declares an
exposure signature (sign of delta, vega, theta) that the optimizer matches
against the regime (VRP sign, user view) instead of v1's lookup table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import bsm

DELTA_GRID = (0.10, 0.16, 0.20, 0.25, 0.30, 0.40)
WIDTH_STEPS = (1, 2, 3, 5)
SINGLE_DELTAS = (0.50, 0.30)


@dataclass
class Opt:
    strike: float
    is_call: bool
    mid: float
    bid: float
    ask: float
    iv: float
    delta: float
    volume: int | None
    oi: int | None
    spread_pct: float | None
    indicative: bool
    illiquid: bool
    liquidity_unknown: bool
    iv_source: str
    raw: dict = field(repr=False, default_factory=dict)

    def leg(self, side: str, qty: int = 1) -> dict:
        kind = "call" if self.is_call else "put"
        return {
            "type": f"{side}_{kind}", "strike": self.strike, "price": self.mid, "qty": qty,
            "iv": self.iv, "ivSource": self.iv_source, "ivVendor": self.raw.get("impliedVolatility"),
            "delta": self.delta, "bid": self.bid, "ask": self.ask, "spreadPct": self.spread_pct,
            "volume": self.volume, "openInterest": self.oi,
            "indicativeOnly": self.indicative, "liquidityUnknown": self.liquidity_unknown,
        }


@dataclass
class Book:
    expiration: str
    T: float
    dte: int
    r: float
    q: float
    spot: float
    forward: float
    calls: list
    puts: list
    atm_iv: float

    def side(self, is_call):
        return self.calls if is_call else self.puts

    def strike_step(self):
        ks = sorted({o.strike for o in self.calls + self.puts})
        if len(ks) < 2:
            return None
        diffs = sorted(b - a for a, b in zip(ks, ks[1:]))
        return diffs[len(diffs) // 2]


def build_book(payload, expiration, chain, *, allow_indicative=False) -> Book | None:
    """Enriched-chain expiry -> Book with smile vol and BSM delta per contract."""
    from datetime import date
    asof = date.fromisoformat(payload["asof"])
    exp = date.fromisoformat(expiration)
    dte = (exp - asof).days
    T = dte / 365.0
    if T <= 0:
        return None
    S = float(payload["price"])
    q = float(payload.get("dividendYield") or 0.0)
    r = float((payload.get("rates") or {}).get(expiration, 0.04))
    F = S * math.exp((r - q) * T)
    smile = (payload.get("smiles") or {}).get(expiration)
    atm_iv = (smile or {}).get("atmIv") or payload.get("atmIvModel") or payload.get("atmIv")
    if not atm_iv:
        return None

    def opts(side_rows, is_call):
        rows = [c for c in side_rows if c.get("mid") and c["mid"] > 0
                and (allow_indicative or (not c.get("illiquid") and not c.get("indicativeOnly")))]
        if not rows:
            return []
        K = np.array([c["strike"] for c in rows], dtype=float)
        iv = np.array([c.get("ivSmile") or c.get("ivModel") or c.get("impliedVolatility") or atm_iv
                       for c in rows], dtype=float)
        delta = np.asarray(bsm.greeks(S, K, T, r, q, iv, is_call)["delta"], dtype=float).reshape(-1)
        out = []
        for c, k, v, d in zip(rows, K, iv, delta):
            src = "smile" if c.get("ivSmile") else ("model" if c.get("ivModel") else "vendor")
            out.append(Opt(float(k), is_call, float(c["mid"]), float(c.get("bid") or 0.0),
                           float(c.get("ask") or 0.0), float(v), float(d),
                           c.get("volume"), c.get("openInterest"), c.get("spreadPct"),
                           bool(c.get("indicativeOnly")), bool(c.get("illiquid")),
                           bool(c.get("liquidityUnknown")), src, c))
        out.sort(key=lambda o: o.strike)
        return out

    return Book(expiration, T, dte, r, q, S, F, opts(chain.get("calls", []), True),
                opts(chain.get("puts", []), False), float(atm_iv))


# ---- selection helpers -------------------------------------------------------------

def by_delta(opts, target, *, otm_only=True):
    """Contract whose |delta| is nearest `target` (OTM side only by default)."""
    cands = [o for o in opts if (abs(o.delta) <= 0.5 + 1e-9 or not otm_only)]
    if not cands:
        return None
    return min(cands, key=lambda o: abs(abs(o.delta) - target))


def at_or_beyond(opts, strike, direction, steps, step_size):
    """Contract `steps` strike-steps away from `strike` in `direction` (+1 up, -1 down)."""
    target = strike + direction * steps * step_size
    pool = [o for o in opts if (o.strike > strike if direction > 0 else o.strike < strike)]
    if not pool:
        return None
    return min(pool, key=lambda o: abs(o.strike - target))


def atm(opts, forward):
    return min(opts, key=lambda o: abs(o.strike - forward)) if opts else None


def paired_atm(book):
    """Call and put at the same strike nearest the forward."""
    put_strikes = {o.strike: o for o in book.puts}
    paired = [c for c in book.calls if c.strike in put_strikes]
    c = atm(paired, book.forward)
    return (c, put_strikes[c.strike]) if c else (None, None)


# ---- structures -----------------------------------------------------------------------
# exposure: (delta sign, vega sign, theta sign); risk: "defined" | "undefined" | "stock"

SPEC = {
    "long_call":          {"exposure": (+1, +1, -1), "risk": "defined",   "legs": 1},
    "long_put":           {"exposure": (-1, +1, -1), "risk": "defined",   "legs": 1},
    "covered_call":       {"exposure": (+1, -1, +1), "risk": "stock",     "legs": 2},
    "cash_secured_put":   {"exposure": (+1, -1, +1), "risk": "stock",     "legs": 1},
    "call_vertical":      {"exposure": (+1,  0, -1), "risk": "defined",   "legs": 2},  # debit
    "put_vertical":       {"exposure": (-1,  0, -1), "risk": "defined",   "legs": 2},  # debit
    "call_credit_spread": {"exposure": (-1, -1, +1), "risk": "defined",   "legs": 2},
    "put_credit_spread":  {"exposure": (+1, -1, +1), "risk": "defined",   "legs": 2},
    "iron_condor":        {"exposure": ( 0, -1, +1), "risk": "defined",   "legs": 4},
    "iron_butterfly":     {"exposure": ( 0, -1, +1), "risk": "defined",   "legs": 4},
    "long_straddle":      {"exposure": ( 0, +1, -1), "risk": "defined",   "legs": 2},
    "short_straddle":     {"exposure": ( 0, -1, +1), "risk": "undefined", "legs": 2},
    "long_strangle":      {"exposure": ( 0, +1, -1), "risk": "defined",   "legs": 2},
    "short_strangle":     {"exposure": ( 0, -1, +1), "risk": "undefined", "legs": 2},
    "calendar":           {"exposure": ( 0, +1, +1), "risk": "defined",   "legs": 2},
    "diagonal":           {"exposure": (+1, +1, +1), "risk": "defined",   "legs": 2},
    "call_ratio_spread":  {"exposure": ( 0, -1, +1), "risk": "undefined", "legs": 2},
}
ALL_STRUCTURES = tuple(SPEC)

# v1 names kept as aliases so saved templates/filters keep working
ALIASES = {"call_vertical": "call_vertical", "put_vertical": "put_vertical"}


def _credit_ok(legs):
    return sum((1 if l["type"].startswith("long") else -1) * l["price"] * l["qty"] for l in legs) < 0


def _debit_ok(legs):
    return sum((1 if l["type"].startswith("long") else -1) * l["price"] * l["qty"] for l in legs) > 0


def build(structure, book: Book, params, far: Book | None = None):
    """Legs for `structure` on `book` (and `far` for calendars/diagonals) or None."""
    step = book.strike_step() or max(book.spot * 0.01, 0.5)
    d = params.get("delta", 0.30)
    w = params.get("width", 1)
    if structure == "long_call":
        o = by_delta(book.calls, d, otm_only=False)
        return [o.leg("long")] if o and o.mid > 0 else None
    if structure == "long_put":
        o = by_delta(book.puts, d, otm_only=False)
        return [o.leg("long")] if o and o.mid > 0 else None
    if structure == "covered_call":
        o = by_delta([c for c in book.calls if c.strike > book.spot], d)
        if not o or o.mid <= 0:
            return None
        return [{"type": "long_stock", "price": book.spot, "qty": 100}, o.leg("short")]
    if structure == "cash_secured_put":
        o = by_delta([p for p in book.puts if p.strike < book.spot], d)
        return [o.leg("short")] if o and o.mid > 0 else None
    if structure == "call_vertical":
        lo = by_delta(book.calls, 0.50, otm_only=False)
        if not lo:
            return None
        hi = at_or_beyond(book.calls, lo.strike, +1, w, step)
        if not hi:
            return None
        legs = [lo.leg("long"), hi.leg("short")]
        return legs if _debit_ok(legs) else None
    if structure == "put_vertical":
        hi = by_delta(book.puts, 0.50, otm_only=False)
        if not hi:
            return None
        lo = at_or_beyond(book.puts, hi.strike, -1, w, step)
        if not lo:
            return None
        legs = [hi.leg("long"), lo.leg("short")]
        return legs if _debit_ok(legs) else None
    if structure == "call_credit_spread":
        sh = by_delta([c for c in book.calls if c.strike > book.spot], d)
        if not sh:
            return None
        lg = at_or_beyond(book.calls, sh.strike, +1, w, step)
        if not lg:
            return None
        legs = [sh.leg("short"), lg.leg("long")]
        return legs if _credit_ok(legs) else None
    if structure == "put_credit_spread":
        sh = by_delta([p for p in book.puts if p.strike < book.spot], d)
        if not sh:
            return None
        lg = at_or_beyond(book.puts, sh.strike, -1, w, step)
        if not lg:
            return None
        legs = [sh.leg("short"), lg.leg("long")]
        return legs if _credit_ok(legs) else None
    if structure in ("iron_condor", "short_strangle", "long_strangle"):
        sp = by_delta([p for p in book.puts if p.strike < book.spot], d)
        sc = by_delta([c for c in book.calls if c.strike > book.spot], d)
        if not sp or not sc:
            return None
        if structure == "short_strangle":
            legs = [sp.leg("short"), sc.leg("short")]
            return legs if _credit_ok(legs) else None
        if structure == "long_strangle":
            legs = [sp.leg("long"), sc.leg("long")]
            return legs if _debit_ok(legs) else None
        lp = at_or_beyond(book.puts, sp.strike, -1, w, step)
        lc = at_or_beyond(book.calls, sc.strike, +1, w, step)
        if not lp or not lc:
            return None
        legs = [lp.leg("long"), sp.leg("short"), sc.leg("short"), lc.leg("long")]
        return legs if _credit_ok(legs) else None
    if structure in ("long_straddle", "short_straddle", "iron_butterfly"):
        c, p = paired_atm(book)
        if not c:
            return None
        if structure == "long_straddle":
            return [c.leg("long"), p.leg("long")]
        if structure == "short_straddle":
            return [p.leg("short"), c.leg("short")]
        lp = at_or_beyond(book.puts, p.strike, -1, w, step)
        lc = at_or_beyond(book.calls, c.strike, +1, w, step)
        if not lp or not lc:
            return None
        legs = [lp.leg("long"), p.leg("short"), c.leg("short"), lc.leg("long")]
        return legs if _credit_ok(legs) else None
    if structure == "call_ratio_spread":
        lo = by_delta(book.calls, 0.50, otm_only=False)
        sh = by_delta([c for c in book.calls if c.strike > book.spot], d)
        if not lo or not sh or sh.strike <= lo.strike:
            return None
        return [lo.leg("long"), sh.leg("short", 2)]
    if structure in ("calendar", "diagonal"):
        if far is None or far.T <= book.T:
            return None
        if structure == "calendar":
            c_near, _ = paired_atm(book)
            if not c_near:
                return None
            c_far = next((o for o in far.calls if o.strike == c_near.strike), None)
            if not c_far or c_far.mid <= c_near.mid:
                return None
            near, farleg = c_near.leg("short"), c_far.leg("long")
        else:
            c_near = by_delta([c for c in book.calls if c.strike > book.spot], d)
            c_far = by_delta(far.calls, 0.50, otm_only=False)
            if not c_near or not c_far or c_far.strike >= c_near.strike or c_far.mid <= c_near.mid:
                return None
            near, farleg = c_near.leg("short"), c_far.leg("long")
        near["expiration"] = book.expiration
        farleg["expiration"] = far.expiration
        farleg["T"] = far.T
        return [near, farleg]
    raise ValueError(f"unknown structure {structure!r}")


def search_grid(structure):
    """Parameter combinations to try for a structure."""
    if structure in ("long_call", "long_put"):
        return [{"delta": d} for d in SINGLE_DELTAS]
    if structure in ("covered_call", "cash_secured_put", "short_strangle", "long_strangle",
                     "call_ratio_spread", "diagonal"):
        return [{"delta": d} for d in DELTA_GRID]
    if structure in ("call_vertical", "put_vertical", "iron_butterfly"):
        return [{"width": w} for w in WIDTH_STEPS]
    if structure in ("call_credit_spread", "put_credit_spread", "iron_condor"):
        return [{"delta": d, "width": w} for d in DELTA_GRID for w in WIDTH_STEPS]
    return [{}]


def eligible(structure, *, vrp_sign=0, view_sign=0, income=False):
    """Exposure-signature match: vega against the VRP sign, delta against the view."""
    ds, vs, ts = SPEC[structure]["exposure"]
    if vrp_sign and vs and vs != -vrp_sign:
        return False  # rich IV -> want short vega (vs = -1); cheap -> long vega
    if view_sign and ds and ds != view_sign:
        return False
    if income and ts <= 0:
        return False
    return True
