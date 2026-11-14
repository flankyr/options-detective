"""Transaction-cost model (Phase 4). Dollars for ONE unit of a position.

- half the bid-ask spread on every option leg (configurable fraction; 0.5 =
  fill at mid is NOT assumed), widened for indicative marks (closed market)
- commission per contract, each way (entry + expected exit/settlement)
- assignment/exercise fee applied with the probability of finishing ITM for
  short legs (expected value of the fee)
A position with `liquidityUnknown` legs (history without volume/OI) gets the
default spread assumption instead of its (absent) book.
"""

from __future__ import annotations

DEFAULTS = {
    "spreadFraction": 0.5,       # of the quoted spread, paid per leg per side
    "commission": 0.65,          # per contract per side
    "assignmentFee": 5.0,        # per assignment event
    "indicativeWidening": 1.5,   # multiply spread cost when marks are indicative
    "defaultSpreadPct": 0.08,    # when no book exists
    "roundTrip": True,           # charge the exit too (close before expiry / settle)
}


def leg_spread_dollars(leg, multiplier=100, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    if leg["type"].endswith("stock"):
        return 0.0
    mid = float(leg.get("price") or 0.0)
    bid, ask = leg.get("bid"), leg.get("ask")
    if bid is not None and ask is not None and ask >= bid > 0:
        spread = float(ask) - float(bid)
    elif leg.get("spreadPct") is not None and mid > 0:
        spread = float(leg["spreadPct"]) * mid
    else:
        spread = cfg["defaultSpreadPct"] * mid
    if leg.get("indicativeOnly"):
        spread *= cfg["indicativeWidening"]
    return cfg["spreadFraction"] * spread * float(leg.get("qty", 1)) * multiplier


def position_costs(legs, *, multiplier=100, cfg=None, prob_itm_short=None):
    """Total expected cost in dollars for one unit. prob_itm_short: list aligned
    with legs, probability each short leg finishes ITM (None -> ignore fee)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    sides = 2 if cfg["roundTrip"] else 1
    spread = sum(leg_spread_dollars(l, multiplier, cfg) for l in legs) * sides
    contracts = sum(float(l.get("qty", 1)) for l in legs if not l["type"].endswith("stock"))
    commission = contracts * cfg["commission"] * sides
    assignment = 0.0
    if prob_itm_short:
        for leg, p in zip(legs, prob_itm_short):
            if leg["type"].startswith("short") and not leg["type"].endswith("stock") and p:
                assignment += cfg["assignmentFee"] * float(p) * float(leg.get("qty", 1))
    return {"spread": round(spread, 4), "commission": round(commission, 4),
            "assignment": round(assignment, 4),
            "total": round(spread + commission + assignment, 4)}
