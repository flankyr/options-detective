"""Volatility smile per expiry: SVI fit with an arbitrage check, spline fallback.

Why a smile at all
------------------
v1 priced probabilities with ONE at-the-money sigma for every strike. Real
chains are skewed (OTM puts trade at higher IV than OTM calls on equities), so
a put spread's market-implied odds were wrong by construction. A fitted smile
gives every strike its own vol, consistently, and is the input to the
market-implied density in Phase 3.

Parameterisation: Gatheral's raw SVI on total implied variance
    w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + s^2)),   k = ln(K / F)
with b >= 0, |rho| < 1, s > 0, a + b*s*sqrt(1 - rho^2) >= 0 (w >= 0).
Butterfly (Durrleman) check g(k) >= 0 on a grid; a violation is reported,
not hidden, and the fit falls back to a shape-preserving spline on IV with
flat wings when there are fewer than MIN_POINTS usable strikes or the SVI
optimiser fails.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

MIN_POINTS = 6
K_GRID = np.linspace(-1.5, 1.5, 121)


def svi_w(k, a, b, rho, m, s):
    k = np.asarray(k, dtype=float)
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + s * s))


def svi_iv(k, params, T):
    w = np.maximum(svi_w(k, *params), 1e-10)
    return np.sqrt(w / T)


def durrleman_g(k, params):
    """Durrleman's butterfly-arbitrage function: g(k) >= 0 means no static arb."""
    a, b, rho, m, s = params
    k = np.asarray(k, dtype=float)
    w = svi_w(k, a, b, rho, m, s)
    root = np.sqrt((k - m) ** 2 + s * s)
    w1 = b * (rho + (k - m) / root)
    w2 = b * s * s / root ** 3
    with np.errstate(divide="ignore", invalid="ignore"):
        g = (1.0 - k * w1 / (2.0 * w)) ** 2 - (w1 ** 2 / 4.0) * (1.0 / w + 0.25) + w2 / 2.0
    return g


def fit_svi(k, iv, T, weights=None):
    """Least-squares SVI fit on total variance. Returns (params, rmse_iv)."""
    k = np.asarray(k, dtype=float)
    w_obs = np.asarray(iv, dtype=float) ** 2 * T
    wts = np.ones_like(k) if weights is None else np.asarray(weights, dtype=float)
    atm = float(np.interp(0.0, k, w_obs)) if k.min() <= 0 <= k.max() else float(np.median(w_obs))
    span = max(k.max() - k.min(), 0.2)
    x0 = np.array([0.5 * atm, max(0.1 * atm / span, 1e-4), -0.3, 0.0, 0.1 * span])
    lo = np.array([-1.0, 0.0, -0.999, -2.0, 1e-4])
    hi = np.array([5.0, 10.0, 0.999, 2.0, 5.0])

    def resid(p):
        a, b, rho, m, s = p
        res = (svi_w(k, a, b, rho, m, s) - w_obs) * wts
        # soft penalty keeps w >= 0 everywhere
        floor = a + b * s * math.sqrt(1.0 - rho * rho)
        return np.append(res, 10.0 * min(floor, 0.0))

    sol = least_squares(resid, x0, bounds=(lo, hi), max_nfev=2000)
    params = tuple(float(x) for x in sol.x)
    rmse = float(np.sqrt(np.mean((svi_iv(k, params, T) - np.asarray(iv)) ** 2)))
    return params, rmse


class Smile:
    """Per-expiry vol function. `iv(k)` on log-forward-moneyness; `iv_strike(K, F)`."""

    def __init__(self, T, method, params=None, spline=None, rmse=0.0, n_used=0,
                 k_range=(0.0, 0.0), arbitrage_free=True, min_g=None):
        self.T = T
        self.method = method
        self.params = params
        self._spline = spline
        self.rmse = rmse
        self.n_used = n_used
        self.k_range = k_range
        self.arbitrage_free = arbitrage_free
        self.min_g = min_g

    def iv(self, k):
        k = np.asarray(k, dtype=float)
        if self.method == "svi":
            return svi_iv(k, self.params, self.T)
        lo, hi = self.k_range
        kc = np.clip(k, lo, hi)  # flat wings beyond the observed strikes
        return np.maximum(self._spline(kc), 1e-4)

    def iv_strike(self, K, F):
        return self.iv(np.log(np.asarray(K, dtype=float) / F))

    def to_dict(self):
        return {
            "method": self.method, "T": self.T, "rmse": self.rmse, "nUsed": self.n_used,
            "kRange": list(self.k_range), "arbitrageFree": self.arbitrage_free, "minG": self.min_g,
            "params": (dict(zip(("a", "b", "rho", "m", "sigma"), self.params)) if self.params else None),
            "knots": ([list(map(float, self._spline.x)), list(map(float, self._spline(self._spline.x)))]
                      if self._spline is not None else None),
        }

    @classmethod
    def from_dict(cls, d):
        if d["method"] == "svi":
            p = d["params"]
            return cls(d["T"], "svi", (p["a"], p["b"], p["rho"], p["m"], p["sigma"]), None,
                       d["rmse"], d["nUsed"], tuple(d["kRange"]), d["arbitrageFree"], d.get("minG"))
        xs, ys = d["knots"]
        return cls(d["T"], d["method"], None, PchipInterpolator(xs, ys, extrapolate=True),
                   d["rmse"], d["nUsed"], tuple(d["kRange"]), d["arbitrageFree"], d.get("minG"))


def fit_smile(k, iv, T, weights=None):
    """Fit one expiry. Falls back to a PCHIP spline on IV when SVI is not viable."""
    k = np.asarray(k, dtype=float)
    iv = np.asarray(iv, dtype=float)
    good = np.isfinite(k) & np.isfinite(iv) & (iv > 0.01) & (iv < 5.0)
    k, iv = k[good], iv[good]
    if weights is not None:
        weights = np.asarray(weights, dtype=float)[good]
    if len(k) == 0:
        raise ValueError("no usable implied volatilities for this expiry")
    order = np.argsort(k)
    k, iv = k[order], iv[order]
    if weights is not None:
        weights = weights[order]
    # collapse duplicate k (same strike on both sides already averaged upstream)
    uk, inv = np.unique(np.round(k, 10), return_inverse=True)
    if len(uk) != len(k):
        iv = np.array([iv[inv == i].mean() for i in range(len(uk))])
        if weights is not None:
            weights = np.array([weights[inv == i].mean() for i in range(len(uk))])
        k = uk
    k_range = (float(k.min()), float(k.max()))
    if len(k) >= MIN_POINTS:
        try:
            params, rmse = fit_svi(k, iv, T, weights)
            g = durrleman_g(K_GRID[(K_GRID >= k_range[0]) & (K_GRID <= k_range[1])], params)
            min_g = float(np.nanmin(g)) if g.size else 0.0
            if rmse < 0.05:  # a fit worse than 5 vol-pts RMSE is not describing this chain
                return Smile(T, "svi", params, None, rmse, len(k), k_range, min_g >= -1e-6, min_g)
        except Exception:
            pass
    if len(k) == 1:
        spline = PchipInterpolator([k[0] - 1.0, k[0], k[0] + 1.0], [iv[0]] * 3)
        return Smile(T, "flat", None, spline, 0.0, 1, (k[0], k[0]), True, None)
    spline = PchipInterpolator(k, iv, extrapolate=True)
    rmse = 0.0  # interpolant passes through the points
    return Smile(T, "spline", None, spline, rmse, len(k), k_range, True, None)


def total_variance_interp(smiles_by_T: dict, T: float, k):
    """Vol at an arbitrary T by linear interpolation of total variance between
    the two bracketing expiries (flat beyond the ends). Standard, keeps
    calendar arbitrage out when the input smiles are themselves ordered."""
    Ts = sorted(smiles_by_T)
    if T <= Ts[0]:
        return smiles_by_T[Ts[0]].iv(k)
    if T >= Ts[-1]:
        return smiles_by_T[Ts[-1]].iv(k)
    for t0, t1 in zip(Ts, Ts[1:]):
        if t0 <= T <= t1:
            w0 = smiles_by_T[t0].iv(k) ** 2 * t0
            w1 = smiles_by_T[t1].iv(k) ** 2 * t1
            w = w0 + (w1 - w0) * (T - t0) / (t1 - t0)
            return np.sqrt(np.maximum(w, 1e-10) / T)
    raise AssertionError("unreachable")
