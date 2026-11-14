"""American-style option pricing: CRR binomial tree and Barone-Adesi-Whaley.

Why both: the tree is the reference (converges to the true American value,
gives tree greeks and the exercise boundary) but costs ~N^2 per contract;
BAW is a closed-form approximation accurate to a few cents on equity options
and vectorises, so the screener can price a whole chain in milliseconds.

US single-stock and ETF options are American; the broad index options
(SPX, XSP, NDX, RUT, VIX, DJX) are European. exercise_style() encodes that.

Inputs follow bsm.py: S, K, T (years), r, q (continuous dividend yield),
sigma, is_call. A continuous yield is a stand-in for discrete dividends; the
error is small for ETFs and for stocks whose ex-date is not inside the
option's life. (Discrete-dividend escrow handling is a Phase 6 item.)
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

import bsm

EUROPEAN_UNDERLYINGS = {"SPX", "SPXW", "XSP", "NDX", "NDXP", "RUT", "RUTW", "VIX", "DJX", "OEX",
                        "I:SPX", "I:NDX", "I:RUT", "I:VIX", "I:XSP"}


def exercise_style(symbol: str) -> str:
    return "european" if str(symbol).upper() in EUROPEAN_UNDERLYINGS else "american"


# ---- CRR binomial ----------------------------------------------------------

def crr_price(S, K, T, r, q, sigma, is_call, steps=300, american=True):
    """Cox-Ross-Rubinstein tree. Returns (price, delta, gamma, theta_per_day)."""
    S, K, T, r, q, sigma = (float(x) for x in (S, K, T, r, q, sigma))
    if T <= 0.0 or sigma <= 0.0:
        intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
        return intrinsic, (1.0 if S > K else 0.0) * (1 if is_call else -1) if S != K else 0.0, 0.0, 0.0
    n = int(steps)
    dt = T / n
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    disc = math.exp(-r * dt)
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0.0 < p < 1.0:
        # dt too coarse for this sigma/carry: fall back to more steps
        return crr_price(S, K, T, r, q, sigma, is_call, steps=n * 2, american=american)
    j = np.arange(n + 1)
    ST = S * u ** (n - j) * d ** j  # terminal nodes, highest first
    V = np.maximum(ST - K, 0.0) if is_call else np.maximum(K - ST, 0.0)
    keep = {}
    for i in range(n - 1, -1, -1):
        V = disc * (p * V[:-1] + (1.0 - p) * V[1:])
        Si = S * u ** (i - np.arange(i + 1)) * d ** np.arange(i + 1)
        if american:
            ex = np.maximum(Si - K, 0.0) if is_call else np.maximum(K - Si, 0.0)
            V = np.maximum(V, ex)
        if i <= 2:
            keep[i] = (Si.copy(), V.copy())
    price = float(keep[0][1][0])
    (S1, V1), (S2, V2) = keep[1], keep[2]
    delta = float((V1[0] - V1[1]) / (S1[0] - S1[1]))
    d_up = (V2[0] - V2[1]) / (S2[0] - S2[1])
    d_dn = (V2[1] - V2[2]) / (S2[1] - S2[2])
    gamma = float((d_up - d_dn) / (0.5 * (S2[0] - S2[2])))
    theta = float((V2[1] - price) / (2.0 * dt) / bsm.DAYS_PER_YEAR)  # per calendar day
    return price, delta, gamma, theta


def crr_greeks(S, K, T, r, q, sigma, is_call, steps=300, american=True):
    price, delta, gamma, theta = crr_price(S, K, T, r, q, sigma, is_call, steps, american)
    up, *_ = crr_price(S, K, T, r, q, sigma + 0.005, is_call, steps, american)
    dn, *_ = crr_price(S, K, T, r, q, max(sigma - 0.005, 1e-6), is_call, steps, american)
    vega = (up - dn) / 1.0  # per vol point: (sigma +/- 0.5pt) spans 1 pt
    rup, *_ = crr_price(S, K, T, r + 0.005, q, sigma, is_call, steps, american)
    rdn, *_ = crr_price(S, K, T, r - 0.005, q, sigma, is_call, steps, american)
    rho = (rup - rdn) / 1.0
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta,
            "vega": vega, "rho": rho}


# ---- Barone-Adesi & Whaley (1987) ------------------------------------------

def _baw_boundary(K, T, r, q, sigma, is_call, iters=40):
    """Vectorised Newton for the BAW critical price S*. NaN where not applicable."""
    b = r - q
    sig2 = sigma * sigma
    with np.errstate(all="ignore"):
        M = 2.0 * r / sig2
        Nn = 2.0 * b / sig2
        Kt = 1.0 - np.exp(-r * T)
        sq = np.sqrt(T)
        root_T = np.sqrt((Nn - 1.0) ** 2 + 4.0 * M / Kt)
        root_inf = np.sqrt((Nn - 1.0) ** 2 + 4.0 * M)
        qq = np.where(is_call, (-(Nn - 1.0) + root_T) / 2.0, (-(Nn - 1.0) - root_T) / 2.0)
        Sinf = np.where(is_call, K / (1.0 - 2.0 / (-(Nn - 1.0) + root_inf)),
                        K / (1.0 - 2.0 / (-(Nn - 1.0) - root_inf)))
        h = np.where(is_call, -(b * T + 2.0 * sigma * sq) * K / (Sinf - K),
                     (b * T - 2.0 * sigma * sq) * K / (K - Sinf))
        Si = np.where(is_call, K + (Sinf - K) * (1.0 - np.exp(h)), Sinf + (K - Sinf) * np.exp(h))
        carry = np.exp((b - r) * T)
        for _ in range(iters):
            d1 = (np.log(Si / K) + (b + 0.5 * sig2) * T) / (sigma * sq)
            euro_i = bsm.price(Si, K, T, r, q, sigma, is_call)
            nd1 = norm.cdf(np.where(is_call, d1, -d1))
            pdf = norm.pdf(d1)
            rhs = np.where(is_call, euro_i + (1.0 - carry * nd1) * Si / qq,
                           euro_i - (1.0 - carry * nd1) * Si / qq)
            bi = np.where(is_call,
                          carry * nd1 * (1.0 - 1.0 / qq) + (1.0 - carry * pdf / (sigma * sq)) / qq,
                          -carry * nd1 * (1.0 - 1.0 / qq) - (1.0 + carry * pdf / (sigma * sq)) / qq)
            nxt = np.where(is_call, (K + rhs - bi * Si) / (1.0 - bi), (K - rhs + bi * Si) / (1.0 + bi))
            good = np.isfinite(nxt) & (nxt > 0)
            Si = np.where(good, nxt, Si)
    return Si, qq, carry, sq, sig2, b


def baw_price(S, K, T, r, q, sigma, is_call):
    """Vectorised BAW American price (arrays broadcast). Returns (price) array/scalar."""
    return _baw(S, K, T, r, q, sigma, is_call)[0]


def _baw(S, K, T, r, q, sigma, is_call):
    S, K, T, r, q, sigma = np.broadcast_arrays(*(np.asarray(x, dtype=float) for x in (S, K, T, r, q, sigma)))
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), S.shape)
    euro = np.asarray(bsm.price(S, K, T, r, q, sigma, is_call), dtype=float)
    intrinsic = np.where(is_call, np.maximum(S - K, 0.0), np.maximum(K - S, 0.0))
    live = (T > 0) & (sigma > 0)
    # calls with no dividend: never exercised early -> European value, no boundary
    no_early = is_call & ((r - q) >= r)
    work = live & ~no_early
    price = np.where(live, euro, intrinsic)
    boundary = np.full(S.shape, np.nan)
    if np.any(work):
        Tw = np.where(work, T, 1.0)
        sw = np.where(work, sigma, 0.2)
        Si, qq, carry, sq, sig2, b = _baw_boundary(K, Tw, r, q, sw, is_call)
        with np.errstate(all="ignore"):
            d1 = (np.log(Si / K) + (b + 0.5 * sig2) * Tw) / (sw * sq)
            nd1 = norm.cdf(np.where(is_call, d1, -d1))
            A = np.where(is_call, (Si / qq) * (1.0 - carry * nd1), -(Si / qq) * (1.0 - carry * nd1))
            early = euro + A * (S / Si) ** qq
            exercised = np.where(is_call, S >= Si, S <= Si)
            approx = np.where(exercised, intrinsic, early)
        ok = work & np.isfinite(approx)
        price = np.where(ok, np.maximum(approx, euro), price)
        boundary = np.where(ok, Si, boundary)
    return bsm._scalar(price), bsm._scalar(boundary)


def critical_price(S, K, T, r, q, sigma, is_call):
    """BAW early-exercise boundary S* (None when early exercise is never optimal)."""
    b = float(_baw(S, K, T, r, q, sigma, is_call)[1])
    return None if math.isnan(b) else b


# ---- JSON-facing wrappers for engine.py -------------------------------------

def american_price(S, K, T, r, sigma, q=0.0, option_type="call", method="crr", steps=300):
    is_call = option_type == "call"
    if method == "baw":
        return float(baw_price(S, K, T, r, q, sigma, is_call))
    return crr_price(S, K, T, r, q, sigma, is_call, steps)[0]


def american_greeks(S, K, T, r, sigma, q=0.0, option_type="call", steps=300):
    is_call = option_type == "call"
    g = crr_greeks(S, K, T, r, q, sigma, is_call, steps)
    euro = float(bsm.price(S, K, T, r, q, sigma, is_call))
    g["europeanPrice"] = euro
    g["earlyExercisePremium"] = max(g["price"] - euro, 0.0)
    g["criticalPrice"] = critical_price(S, K, T, r, q, sigma, is_call)
    g = {k: (float(v) if isinstance(v, (np.floating, float, int)) else v) for k, v in g.items()}
    return g
