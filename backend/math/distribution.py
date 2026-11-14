"""Terminal-price distributions and position metrics under them (Phase 3).

Three distributions matter to the app:

1. MARKET-IMPLIED  — Breeden-Litzenberger: the risk-neutral density is the
   second strike-derivative of the call price curve, pdf(K) = e^{rT} C_KK(K).
   Built from the fitted smile (Phase 2) so it inherits the skew. Under this
   density every option priced on the same smile has expected payoff equal to
   its forward price: EV after financing is ZERO by construction. That is the
   mathematical reason v1's POP-weighted score had no edge, and the first
   test in test_distribution.py pins it.

2. SCENARIO — the market density reshaped by what WE forecast: log-returns
   about the forward are rescaled by sigma_forecast / sigma_implied (keeps the
   skew and kurtosis shape, changes the dispersion) and shifted by a drift
   tilt from the user's view. VRP mode = rescale only; Directional mode =
   shift only; Both = both.

3. LOGNORMAL — the v1 baseline (single sigma), kept for comparisons.

A Distribution is a price grid `x` (ascending) with a normalised density
`pdf`; integration is trapezoidal. Metrics for a set of legs are computed on
the same grid: EV (expected P&L after financing the entry at r), POP,
P(max profit), CVaR at 5%, EV/CVaR, expected move, and a Kelly fraction.
"""

from __future__ import annotations

import math

import numpy as np

import bsm
from smile import Smile

GRID_POINTS = 4001
GRID_SIGMAS = 7.0
CVAR_LEVEL = 0.05
CONTRACT_MULTIPLIER = 100


class Distribution:
    def __init__(self, x, pdf, *, kind, params=None):
        x = np.asarray(x, dtype=float)
        pdf = np.maximum(np.asarray(pdf, dtype=float), 0.0)
        mass = np.trapezoid(pdf, x) if hasattr(np, "trapezoid") else np.trapz(pdf, x)
        if not math.isfinite(mass) or mass <= 0:
            raise ValueError("density has no mass")
        self.x = x
        self.pdf = pdf / mass
        self.kind = kind
        self.params = params or {}
        self._cdf = np.concatenate([[0.0], np.cumsum(0.5 * (self.pdf[1:] + self.pdf[:-1]) * np.diff(x))])
        self._cdf /= self._cdf[-1]

    # ---- probability helpers --------------------------------------------------
    def cdf(self, level):
        return float(np.interp(level, self.x, self._cdf, left=0.0, right=1.0))

    def prob_between(self, lo, hi):
        return max(0.0, self.cdf(hi) - self.cdf(lo))

    def expectation(self, values):
        values = np.asarray(values, dtype=float)
        return float(_trapz(values * self.pdf, self.x))

    @property
    def mean(self):
        return self.expectation(self.x)

    @property
    def std(self):
        m = self.mean
        return math.sqrt(max(self.expectation((self.x - m) ** 2), 0.0))

    def quantile(self, p):
        return float(np.interp(p, self._cdf, self.x))

    def log_moments(self, forward):
        lr = np.log(self.x / forward)
        m = self.expectation(lr)
        s = math.sqrt(max(self.expectation((lr - m) ** 2), 1e-18))
        return m, s

    def to_dict(self, points=121):
        """Downsampled for the UI: quantile-spaced x, pdf, cdf."""
        idx = np.unique(np.linspace(0, len(self.x) - 1, points).astype(int))
        return {
            "kind": self.kind, "params": self.params,
            "x": [float(v) for v in self.x[idx]],
            "pdf": [float(v) for v in self.pdf[idx]],
            "cdf": [float(v) for v in self._cdf[idx]],
            "mean": self.mean, "std": self.std,
            "q05": self.quantile(0.05), "q50": self.quantile(0.5), "q95": self.quantile(0.95),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["pdf"], kind=d.get("kind", "custom"), params=d.get("params"))


def _trapz(y, x):
    return np.trapezoid(y, x) if hasattr(np, "trapezoid") else np.trapz(y, x)


def price_grid(S, T, sigma, n=GRID_POINTS, sigmas=GRID_SIGMAS):
    span = max(sigma, 0.05) * math.sqrt(max(T, 1 / 365)) * sigmas
    return S * np.exp(np.linspace(-span, span, n))


# ---- 1. market-implied (Breeden-Litzenberger) --------------------------------

def implied_density(smile, S, T, r, q, n=GRID_POINTS):
    """Risk-neutral density from a fitted smile. `smile` is a Smile or its dict."""
    sm = smile if isinstance(smile, Smile) else Smile.from_dict(smile)
    F = S * math.exp((r - q) * T)
    atm = float(sm.iv(0.0))
    # the grid must reach where the WING vols put mass, not just the ATM vol:
    # widen until the endpoint vols are covered (capped at k = +/-4)
    span = max(atm, 0.05) * math.sqrt(max(T, 1 / 365)) * GRID_SIGMAS
    for _ in range(6):
        wing = float(max(sm.iv(-span), sm.iv(span), atm))
        new_span = min(wing * math.sqrt(max(T, 1 / 365)) * GRID_SIGMAS, 4.0)
        if new_span <= span * 1.02:
            break
        span = new_span
    K = F * np.exp(np.linspace(-span, span, n))
    vols = sm.iv_strike(K, F)
    C = bsm.price(S, K, T, r, q, vols, True)
    # second derivative in K on a non-uniform grid
    dK = np.diff(K)
    dC = np.diff(C)
    first = dC / dK                       # at midpoints
    Km = 0.5 * (K[1:] + K[:-1])
    second = np.diff(first) / np.diff(Km)  # at interior nodes K[1:-1]
    pdf = math.exp(r * T) * second
    negative_mass = float(-_trapz(np.minimum(pdf, 0.0), K[1:-1]))
    pdf = np.maximum(pdf, 0.0)             # a locally arbitrageable fit shows as negative mass
    x = K[1:-1]
    raw_mass = float(_trapz(pdf, x))
    dist = Distribution(x, pdf, kind="market", params={
        "atmIv": atm, "forward": F, "T": T, "r": r, "q": q, "smileMethod": sm.method,
        # diagnostics: how much the fit had to be repaired. rawMass should be ~1
        # (truncation/arbitrage lose mass), negativeMass ~0, meanError ~0
        # (the risk-neutral mean must be the forward).
        "rawMass": round(raw_mass, 6), "negativeMass": round(negative_mass, 6)})
    dist.params["meanError"] = round(dist.mean / F - 1.0, 6)
    return dist


def lognormal_density(S, T, sigma, drift=0.0, n=GRID_POINTS):
    """Lognormal with annualised log-drift `drift` (0 = zero-drift convention)."""
    x = price_grid(S, T, sigma, n)
    mu = math.log(S) + (drift - 0.5 * sigma * sigma) * T
    s = sigma * math.sqrt(T)
    pdf = np.exp(-0.5 * ((np.log(x) - mu) / s) ** 2) / (x * s * math.sqrt(2 * math.pi))
    return Distribution(x, pdf, kind="lognormal", params={"sigma": sigma, "drift": drift, "T": T})


# ---- 2. scenario ------------------------------------------------------------------

def scenario_density(market: Distribution, forward, T, *, sigma_forecast=None, drift_shift=0.0):
    """Reshape the market density: scale log-dispersion to the forecast vol and
    tilt the mean by `drift_shift` (annualised log-return). Shape (skew,
    kurtosis) is preserved; the Jacobian keeps the density proper."""
    m, s_impl = market.log_moments(forward)
    s_impl_ann = s_impl / math.sqrt(T)
    scale = 1.0 if sigma_forecast is None else max(sigma_forecast, 1e-4) / s_impl_ann
    lr = np.log(market.x / forward)
    new_lr = m + (lr - m) * scale + drift_shift * T
    y = forward * np.exp(new_lr)
    jac = (y / market.x) * scale           # dy/dx
    pdf = market.pdf / jac
    return Distribution(y, pdf, kind="scenario", params={
        **market.params, "sigmaForecast": sigma_forecast, "sigmaImplied": s_impl_ann,
        "scale": scale, "driftShift": drift_shift})


def view_drift(view, conviction, sigma, T):
    """Directional view -> annualised log-drift tilt.
    conviction in [0, 1] scales a one-sigma-over-the-horizon move: a fully
    convinced bull expects the forward to land one sigma higher at expiry."""
    sign = {"bullish": 1.0, "bearish": -1.0}.get(view, 0.0)
    c = min(max(float(conviction), 0.0), 1.0)
    if sign == 0.0 or c == 0.0 or T <= 0:
        return 0.0
    return sign * c * sigma * math.sqrt(T) / T


# ---- 3. position metrics ----------------------------------------------------------

def payoff_grid(legs, x, multiplier=CONTRACT_MULTIPLIER):
    """Expiry payoff of the whole position at every grid price (per unit = 1 lot)."""
    x = np.asarray(x, dtype=float)
    total = np.zeros_like(x)
    for leg in legs:
        t = leg["type"]
        sign = 1.0 if t.startswith("long") else -1.0
        qty = float(leg.get("qty", 1))
        price = float(leg.get("price", 0.0))
        if t.endswith("stock"):
            total += sign * qty * (x - price)
            continue
        K = float(leg["strike"])
        intrinsic = np.maximum(x - K, 0.0) if t.endswith("call") else np.maximum(K - x, 0.0)
        total += sign * qty * multiplier * (intrinsic - price)
    return total


def entry_cost(legs, multiplier=CONTRACT_MULTIPLIER):
    """Net debit (+) / credit (-) paid at entry for one unit."""
    total = 0.0
    for leg in legs:
        sign = 1.0 if leg["type"].startswith("long") else -1.0
        scale = float(leg.get("qty", 1)) * (1.0 if leg["type"].endswith("stock") else multiplier)
        total += sign * float(leg.get("price", 0.0)) * scale
    return total


def position_metrics(legs, dist: Distribution, *, r=0.0, T=0.0, costs=0.0,
                     capital=None, multiplier=CONTRACT_MULTIPLIER):
    """All the numbers the optimizer ranks on, under one distribution.

    `costs` = transaction costs for one unit (half-spreads + commissions), in
    dollars, subtracted from P&L. `capital` = dollars at risk for one unit
    (max loss or buying power) used for return-on-capital and Kelly.
    EV is in dollars at expiry; the entry cost is financed at r over T, so a
    position priced exactly on the market density has EV == 0 before costs.
    """
    pnl_raw = payoff_grid(legs, dist.x, multiplier)   # payoff minus premium (undiscounted)
    cost0 = entry_cost(legs, multiplier)
    financing = cost0 * (math.exp(r * T) - 1.0)       # carry on the premium paid/received
    pnl = pnl_raw - financing - costs
    ev = dist.expectation(pnl)
    pop = dist.expectation((pnl > 0).astype(float))
    max_pnl = float(pnl.max())
    p_max = dist.expectation((pnl >= max_pnl - 1e-9 * max(abs(max_pnl), 1)).astype(float)) \
        if np.isfinite(max_pnl) else 0.0
    # CVaR: expected P&L in the worst 5% of outcomes (negative number = loss)
    order = np.argsort(pnl)
    w = np.concatenate([[0.0], np.cumsum(0.5 * (dist.pdf[1:] + dist.pdf[:-1]) * np.diff(dist.x))])
    # weights per grid node (trapezoid shares), then sort by pnl
    node_w = np.gradient(w, edge_order=1) if len(w) > 2 else np.ones_like(w) / len(w)
    node_w = np.maximum(node_w, 0.0)
    node_w /= node_w.sum()
    sw = node_w[order]
    cum = np.cumsum(sw)
    tail = cum <= CVAR_LEVEL
    if not np.any(tail):
        tail[0] = True
    cvar = float(np.sum(pnl[order][tail] * sw[tail]) / max(np.sum(sw[tail]), 1e-12))
    var5 = float(pnl[order][np.searchsorted(cum, CVAR_LEVEL)] if cum[-1] >= CVAR_LEVEL else pnl[order][0])
    ev_per_cvar = ev / abs(cvar) if cvar < 0 else (math.inf if ev > 0 else 0.0)
    cap = float(capital) if capital else max(-float(pnl.min()), 1.0)
    ret = pnl / cap
    mu, var = dist.expectation(ret), dist.expectation(ret ** 2) - dist.expectation(ret) ** 2
    kelly = float(min(max(mu / var, 0.0), 1.0)) if var > 1e-12 else 0.0
    return {
        "ev": round(ev, 4),
        "evPct": round(ev / cap, 6),
        "pop": round(pop, 6),
        "probMaxProfit": round(p_max, 6),
        "maxProfit": None if max_pnl > 1e12 else round(max_pnl, 2),
        "maxLoss": None if pnl.min() < -1e12 else round(-float(pnl.min()), 2),
        "cvar05": round(cvar, 4),
        "var05": round(var5, 4),
        "evPerCvar": None if not math.isfinite(ev_per_cvar) else round(ev_per_cvar, 6),
        "kellyFraction": round(kelly, 6),
        "expectedMove": round(dist.std, 4),
        "entryCost": round(cost0, 2),
        "financing": round(financing, 4),
        "costs": round(costs, 4),
        "distribution": dist.kind,
    }


# ---- engine.py entry points ----------------------------------------------------------

def implied_density_json(smile, S, T, r, q=0.0, points=121):
    return implied_density(smile, S, T, r, q).to_dict(points)


def evaluate_position(legs, S, T, r, q=0.0, smile=None, sigma=None, *, mode="market",
                      sigma_forecast=None, view="neutral", conviction=0.0, costs=0.0,
                      capital=None, include_distribution=False):
    """One call for the Node side: metrics under the market density and, when
    mode != 'market', under the scenario density (vrp | directional | both)."""
    F = S * math.exp((r - q) * T)
    if smile is not None:
        market = implied_density(smile, S, T, r, q)
    elif sigma is not None:
        market = lognormal_density(S, T, sigma, drift=(r - q))
    else:
        raise ValueError("either smile or sigma is required")
    out = {"market": position_metrics(legs, market, r=r, T=T, costs=costs, capital=capital)}
    out["forward"] = F
    if mode != "market":
        _, s_impl = market.log_moments(F)
        s_impl_ann = s_impl / math.sqrt(T) if T > 0 else 0.0
        sf = sigma_forecast if mode in ("vrp", "both") and sigma_forecast else None
        drift = view_drift(view, conviction, sf or s_impl_ann, T) if mode in ("directional", "both") else 0.0
        scen = scenario_density(market, F, T, sigma_forecast=sf, drift_shift=drift)
        out["scenario"] = position_metrics(legs, scen, r=r, T=T, costs=costs, capital=capital)
        out["scenarioParams"] = {"mode": mode, "sigmaImplied": s_impl_ann, "sigmaForecast": sf,
                                 "view": view, "conviction": conviction, "driftShift": drift}
        if include_distribution:
            out["scenarioDistribution"] = scen.to_dict()
    if include_distribution:
        out["marketDistribution"] = market.to_dict()
    return out
