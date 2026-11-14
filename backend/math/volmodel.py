"""Chain -> implied vols we solved ourselves -> one smile per expiry.

enrich(payload) adds to a schema-v2 chain payload:
  per contract : ivModel (BSM IV from mid, else last), ivSmile (fitted smile vol
                 at that strike: the vol the engine USES), ivFlag
  per expiry   : payload["smiles"][exp] = Smile.to_dict() + forward, atmIv, T, r, q
  top level    : atmIvModel (nearest expiry's smile ATM vol), volModel summary

ivModel is a European (BSM) implied vol even for American-style underlyings.
That is the market convention for quoting IV; the American premium shows up
in the pricer (american.py), not in the vol.

The smile is fitted on out-of-the-money contracts (puts below the forward,
calls above): the liquid, informative side of each strike. Weights favour
tight books. Contracts whose solved IV differs from the vendor's by more than
VENDOR_TOL (relative) are flagged `vendorDeviation`; those with no solvable IV
(mid outside the no-arbitrage band, usually a stale or crossed book) are
flagged `noIv` and excluded from the fit.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np

import bsm
from smile import Smile, fit_smile, total_variance_interp

VENDOR_TOL = 0.15
MIN_FIT_MID = 0.05
DEFAULT_R = 0.04


def _T(exp: str, asof: date) -> float:
    return max((date.fromisoformat(exp) - asof).days, 0) / 365.0


def enrich(payload: dict) -> dict:
    asof = date.fromisoformat(payload["asof"])
    S = float(payload["price"])
    q = float(payload.get("dividendYield") or 0.0)
    rates = payload.get("rates") or {}
    smiles = {}
    summary = {"expirations": 0, "contracts": 0, "solved": 0, "vendorDeviations": 0, "noIv": 0}
    smiles_by_T = {}
    for exp in payload["expirations"]:
        chain = payload["chains"][exp]
        T = _T(exp, asof)
        r = float(rates.get(exp, DEFAULT_R))
        F = S * math.exp((r - q) * T)
        contracts = [(c, True) for c in chain["calls"]] + [(c, False) for c in chain["puts"]]
        summary["contracts"] += len(contracts)
        if T <= 0 or not contracts:
            continue
        K = np.array([c["strike"] for c, _ in contracts], dtype=float)
        mids = np.array([c["mid"] if c.get("mid") else np.nan for c, _ in contracts], dtype=float)
        is_call = np.array([ic for _, ic in contracts], dtype=bool)
        iv = bsm.implied_vol(np.where(np.isfinite(mids), mids, 0.0), S, K, T, r, q, is_call)
        iv = np.where(np.isfinite(mids), iv, np.nan)
        iv = np.where(iv > 0.005, iv, np.nan)  # zero/near-zero vol = price at intrinsic, not a vol
        k = np.log(K / F)
        otm = np.where(is_call, k >= 0, k <= 0)
        spread = np.array([c.get("spreadPct") if c.get("spreadPct") is not None else 0.2
                           for c, _ in contracts], dtype=float)
        weights = 1.0 / np.clip(spread, 0.005, 1.0)
        # penny options carry no vol information (a 1c book on a 5c option is a
        # 20-vol-point error bar); keep them out of the fit, they still get ivSmile
        usable = np.isfinite(iv) & otm & (mids >= MIN_FIT_MID)
        smile = None
        if np.any(usable):
            try:
                smile = fit_smile(k[usable], iv[usable], T, weights[usable])
            except ValueError:
                smile = None
        if smile is None and np.any(np.isfinite(iv)):
            smile = fit_smile(k[np.isfinite(iv)], iv[np.isfinite(iv)], T)
        iv_smile = smile.iv(k) if smile is not None else np.full(len(k), np.nan)
        for i, (c, _) in enumerate(contracts):
            model = None if not np.isfinite(iv[i]) else round(float(iv[i]), 6)
            c["ivModel"] = model
            c["ivSmile"] = None if not np.isfinite(iv_smile[i]) else round(float(iv_smile[i]), 6)
            vendor = c.get("ivVendor")
            flag = None
            if model is None:
                flag = "noIv"
                summary["noIv"] += 1
            elif vendor and abs(model - vendor) / vendor > VENDOR_TOL:
                flag = "vendorDeviation"
                summary["vendorDeviations"] += 1
            else:
                summary["solved"] += 1
            c["ivFlag"] = flag
        if smile is not None:
            d = smile.to_dict()
            d.update({"expiration": exp, "forward": round(F, 4), "r": r, "q": q,
                      "atmIv": round(float(smile.iv(0.0)), 6)})
            smiles[exp] = d
            smiles_by_T[T] = smile
            summary["expirations"] += 1
    payload["smiles"] = smiles
    payload["volModel"] = {**summary, "method": "svi+spline", "ivConvention": "BSM European from mid"}
    if smiles:
        first = min(smiles.values(), key=lambda d: d["T"])
        payload["atmIvModel"] = first["atmIv"]
        # term structure at 30 calendar days, a common reference
        payload["atmIv30"] = round(float(total_variance_interp(smiles_by_T, 30 / 365.0, 0.0)), 6)
    else:
        payload["atmIvModel"] = None
        payload["atmIv30"] = None
    return payload


# ---- engine.py entry points ---------------------------------------------------

def smile_iv(smile: dict, strikes, forward=None):
    """Evaluate a serialized smile at strikes (uses its stored forward by default)."""
    sm = Smile.from_dict(smile)
    F = float(forward if forward is not None else smile["forward"])
    return [float(x) for x in np.atleast_1d(sm.iv_strike(np.asarray(strikes, dtype=float), F))]


def smile_fit(k, iv, T, weights=None):
    sm = fit_smile(k, iv, T, weights)
    return sm.to_dict()
