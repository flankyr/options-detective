"""Black-Scholes-Merton (continuous dividend yield) — the v2 pricing core.

Differences from black_scholes.py (v1, kept intact for its 1,300 tests):
- carries a dividend yield `q` and takes `r` per expiry (from data/rates.py)
- vectorised over numpy arrays so a whole chain prices in one call
- the same trader-unit greeks: delta per $1, gamma per $1, theta per calendar
  day, vega per vol point, rho per rate point

Degenerate inputs follow v1: T <= 0 -> intrinsic, sigma == 0 -> discounted
forward intrinsic, step-function delta, other greeks zero.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

DAYS_PER_YEAR = 365.0


def _arr(*xs):
    return [np.asarray(x, dtype=float) for x in xs]


def _validate(S, K, T, r, q, sigma):
    S, K, T, r, q, sigma = _arr(S, K, T, r, q, sigma)
    for name, v in (("S", S), ("K", K), ("T", T), ("r", r), ("q", q), ("sigma", sigma)):
        if not np.all(np.isfinite(v)):
            raise ValueError(f"{name} must be finite")
    if np.any(S <= 0):
        raise ValueError("S must be > 0")
    if np.any(K <= 0):
        raise ValueError("K must be > 0")
    if np.any(sigma < 0):
        raise ValueError("sigma must be >= 0")
    return S, K, np.maximum(T, 0.0), r, q, sigma


def _d1d2(S, K, T, r, q, sigma):
    with np.errstate(divide="ignore", invalid="ignore"):
        sq = np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sq)
        d2 = d1 - sigma * sq
    return d1, d2, sq


def price(S, K, T, r, q, sigma, is_call):
    """BSM price; arrays broadcast. `is_call` bool or bool array."""
    S, K, T, r, q, sigma = _validate(S, K, T, r, q, sigma)
    is_call = np.asarray(is_call, dtype=bool)
    live = (T > 0) & (sigma > 0)
    d1, d2, _ = _d1d2(S, K, np.where(live, T, 1.0), r, q, np.where(live, sigma, 1.0))
    df_r = np.exp(-r * T)
    df_q = np.exp(-q * T)
    call = S * df_q * norm.cdf(d1) - K * df_r * norm.cdf(d2)
    put = K * df_r * norm.cdf(-d2) - S * df_q * norm.cdf(-d1)
    # degenerate: intrinsic on the forward (T==0 -> plain intrinsic since df=1)
    call_deg = np.maximum(S * df_q - K * df_r, 0.0)
    put_deg = np.maximum(K * df_r - S * df_q, 0.0)
    out = np.where(is_call, np.where(live, call, call_deg), np.where(live, put, put_deg))
    return _scalar(out)


def greeks(S, K, T, r, q, sigma, is_call):
    """Dict of arrays (or floats): delta, gamma, theta (per day), vega (per pt), rho (per pt)."""
    S, K, T, r, q, sigma = _validate(S, K, T, r, q, sigma)
    is_call = np.asarray(is_call, dtype=bool)
    live = (T > 0) & (sigma > 0)
    Tl = np.where(live, T, 1.0)
    sl = np.where(live, sigma, 1.0)
    d1, d2, sq = _d1d2(S, K, Tl, r, q, sl)
    df_r = np.exp(-r * Tl)
    df_q = np.exp(-q * Tl)
    pdf = norm.pdf(d1)
    delta_c = df_q * norm.cdf(d1)
    delta = np.where(is_call, delta_c, delta_c - df_q)
    gamma = df_q * pdf / (S * sl * sq)
    theta_common = -S * df_q * pdf * sl / (2.0 * sq)
    theta_c = theta_common - r * K * df_r * norm.cdf(d2) + q * S * df_q * norm.cdf(d1)
    theta_p = theta_common + r * K * df_r * norm.cdf(-d2) - q * S * df_q * norm.cdf(-d1)
    theta = np.where(is_call, theta_c, theta_p) / DAYS_PER_YEAR
    vega = S * df_q * pdf * sq / 100.0
    rho = np.where(is_call, K * Tl * df_r * norm.cdf(d2), -K * Tl * df_r * norm.cdf(-d2)) / 100.0
    # degenerate branch: step delta on the forward moneyness, rest zero
    fwd_itm = S * np.exp(-q * T) > K * np.exp(-r * T)
    fwd_atm = np.isclose(S * np.exp(-q * T), K * np.exp(-r * T))
    step = np.where(fwd_atm, 0.5, np.where(fwd_itm, 1.0, 0.0))
    deg_delta = np.where(is_call, step, step - 1.0)
    z = np.zeros_like(delta)
    out = {
        "delta": np.where(live, delta, deg_delta),
        "gamma": np.where(live, gamma, z),
        "theta": np.where(live, theta, z),
        "vega": np.where(live, vega, z),
        "rho": np.where(live, rho, z),
    }
    return {k: _scalar(v) for k, v in out.items()}


def _scalar(a):
    a = np.asarray(a)
    return float(a) if a.ndim == 0 else a


def implied_vol(market_price, S, K, T, r, q, is_call, tol=1e-8, max_iter=50):
    """Vectorised IV: Newton from the Brenner-Subrahmanyam seed with a bisection
    polish for the stragglers. Returns NaN where no vol reproduces the price
    (below intrinsic, above the upper bound, or T <= 0)."""
    P, S, K, T, r, q = _arr(market_price, S, K, T, r, q)
    P, S, K, T, r, q = np.broadcast_arrays(P, S, K, T, r, q)
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), P.shape)
    df_r = np.exp(-r * T)
    df_q = np.exp(-q * T)
    fwd_c = S * df_q
    floor = np.where(is_call, np.maximum(fwd_c - K * df_r, 0.0), np.maximum(K * df_r - fwd_c, 0.0))
    ceil = np.where(is_call, fwd_c, K * df_r)
    eps = 1e-10 * np.maximum(1.0, np.maximum(S, K))
    ok = (T > 0) & np.isfinite(P) & (P >= floor - eps) & (P < ceil - eps)
    at_floor = ok & (P <= floor + eps)
    sigma = np.full(P.shape, np.nan)
    sigma[at_floor] = 0.0
    work = ok & ~at_floor
    if not np.any(work):
        return _scalar(sigma)
    s = np.maximum(1e-3, np.sqrt(2.0 * np.pi / T[work]) * P[work] / S[work])
    lo = np.full(s.shape, 1e-6)
    hi = np.full(s.shape, 10.0)
    Sw, Kw, Tw, rw, qw, Pw, cw = S[work], K[work], T[work], r[work], q[work], P[work], is_call[work]
    for _ in range(max_iter):
        diff = price(Sw, Kw, Tw, rw, qw, s, cw) - Pw
        # maintain a bracket for the bisection fallback
        lo = np.where(diff < 0, np.maximum(lo, s), lo)
        hi = np.where(diff > 0, np.minimum(hi, s), hi)
        d1, _, sq = _d1d2(Sw, Kw, Tw, rw, qw, s)
        vega = Sw * np.exp(-qw * Tw) * norm.pdf(d1) * sq
        step = np.where(vega > 1e-12, diff / np.maximum(vega, 1e-12), 0.0)
        nxt = s - step
        bad = (vega <= 1e-12) | (nxt <= lo) | (nxt >= hi) | ~np.isfinite(nxt)
        nxt = np.where(bad, 0.5 * (lo + hi), nxt)
        if np.all(np.abs(diff) < tol) or np.all(np.abs(nxt - s) < 1e-12):
            s = nxt
            break
        s = nxt
    # polish: 40 bisection steps where Newton did not converge
    diff = price(Sw, Kw, Tw, rw, qw, s, cw) - Pw
    unconverged = np.abs(diff) >= tol
    if np.any(unconverged):
        lo_u, hi_u = lo[unconverged], hi[unconverged]
        for _ in range(60):
            mid = 0.5 * (lo_u + hi_u)
            dm = price(Sw[unconverged], Kw[unconverged], Tw[unconverged], rw[unconverged],
                       qw[unconverged], mid, cw[unconverged]) - Pw[unconverged]
            lo_u = np.where(dm < 0, mid, lo_u)
            hi_u = np.where(dm >= 0, mid, hi_u)
        s[unconverged] = 0.5 * (lo_u + hi_u)
    sigma[work] = s
    return _scalar(sigma)


# ---- JSON-facing wrappers for engine.py -------------------------------------

def bsm_price(S, K, T, r, sigma, q=0.0, option_type="call"):
    return float(price(S, K, T, r, q, sigma, option_type == "call"))


def bsm_greeks(S, K, T, r, sigma, q=0.0, option_type="call"):
    return {k: float(v) for k, v in greeks(S, K, T, r, q, sigma, option_type == "call").items()}


def bsm_implied_vol(market_price, S, K, T, r, q=0.0, option_type="call"):
    v = float(implied_vol(market_price, S, K, T, r, q, option_type == "call"))
    if math.isnan(v):
        raise ValueError("no volatility reproduces this price (outside no-arbitrage bounds or expired)")
    return v
