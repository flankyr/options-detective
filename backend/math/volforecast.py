"""Realized-volatility forecasts and the volatility risk premium (Phase 3).

The edge model's first leg: compare what the market charges for variance
(implied vol) with what the underlying is likely to deliver (a forecast of
realized vol). Three forecasters, all on daily closes, all returning an
annualised sigma for the next `horizon` trading days plus a confidence band
from their own historical forecast errors:

- HAR-RV (Corsi 2009), default: regress forward average variance on the
  daily, weekly (5d) and monthly (22d) averages of squared returns. Few
  parameters, robust, captures the long memory of vol.
- EWMA (RiskMetrics, lambda = 0.94): a flat forecast at today's smoothed
  variance. The baseline every forecaster should beat.
- GARCH(1,1) by maximum likelihood (scipy, no extra dependency), with the
  horizon-average variance from the recursion.

Realized vol here is close-to-close (no intraday data in the free sources);
annualisation uses 252 trading days.

VRP = iv_atm - sigma_forecast at the same horizon. Positive = the market pays
more for variance than the forecast says it will realize (sell premium);
negative = options look cheap (buy premium). The z-score needs that symbol's
own history of VRP, which the archive accumulates one day at a time
(archive.py stores atmIv per snapshot).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize

TRADING_DAYS = 252
HAR_LAGS = (1, 5, 22)
MIN_OBS = 60


def log_returns(closes):
    c = np.asarray(closes, dtype=float)
    c = c[np.isfinite(c) & (c > 0)]
    return np.diff(np.log(c))


def realized_vol(returns, window=21):
    """Trailing annualised close-to-close vol over the last `window` returns."""
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return float("nan")
    w = r[-window:]
    return float(np.sqrt(np.mean(w * w) * TRADING_DAYS))


def realized_vol_series(returns, window=21):
    r = np.asarray(returns, dtype=float)
    if len(r) < window:
        return np.array([])
    sq = r * r
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    return np.sqrt((cs[window:] - cs[:-window]) / window * TRADING_DAYS)


# ---- EWMA ---------------------------------------------------------------------------

def ewma_variance(returns, lam=0.94):
    r = np.asarray(returns, dtype=float)
    v = float(np.mean(r[:min(len(r), 22)] ** 2)) if len(r) else 0.0
    for x in r:
        v = lam * v + (1.0 - lam) * x * x
    return v


def ewma_forecast(returns, horizon=21, lam=0.94):
    r = np.asarray(returns, dtype=float)
    if len(r) < 22:
        raise ValueError(f"need at least 22 returns, got {len(r)}")
    v = ewma_variance(r, lam)
    sigma = math.sqrt(v * TRADING_DAYS)
    return {"method": "ewma", "sigma": sigma, "horizon": horizon, "lambda": lam, "nObs": len(r),
            **_error_band(r, horizon, lambda hist: math.sqrt(ewma_variance(hist, lam) * TRADING_DAYS))}


# ---- HAR-RV ---------------------------------------------------------------------------

def _har_design(sq, horizon):
    """Rows: t; X = [1, mean sq over last 1, 5, 22 days]; y = mean sq over next `horizon` days."""
    n = len(sq)
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    rows, ys = [], []
    for t in range(HAR_LAGS[-1], n - horizon + 1):
        feats = [1.0] + [(cs[t] - cs[t - lag]) / lag for lag in HAR_LAGS]
        rows.append(feats)
        ys.append((cs[t + horizon] - cs[t]) / horizon)
    return np.array(rows), np.array(ys)


def har_fit(returns, horizon=21):
    r = np.asarray(returns, dtype=float)
    sq = r * r
    X, y = _har_design(sq, horizon)
    if len(y) < 30:
        raise ValueError(f"need at least {HAR_LAGS[-1] + horizon + 30} returns for HAR, got {len(r)}")
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def har_forecast(returns, horizon=21):
    r = np.asarray(returns, dtype=float)
    beta = har_fit(r, horizon)
    sq = r * r
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    n = len(sq)
    feats = np.array([1.0] + [(cs[n] - cs[n - lag]) / lag for lag in HAR_LAGS])
    var_fwd = max(float(feats @ beta), 1e-8)
    sigma = math.sqrt(var_fwd * TRADING_DAYS)
    return {"method": "har", "sigma": sigma, "horizon": horizon, "beta": [float(b) for b in beta],
            "nObs": len(r),
            **_error_band(r, horizon, lambda hist: har_forecast_point(hist, horizon, beta))}


def har_forecast_point(returns, horizon, beta):
    sq = np.asarray(returns, dtype=float) ** 2
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    n = len(sq)
    feats = np.array([1.0] + [(cs[n] - cs[n - lag]) / lag for lag in HAR_LAGS])
    return math.sqrt(max(float(feats @ beta), 1e-8) * TRADING_DAYS)


# ---- GARCH(1,1) -------------------------------------------------------------------------

def garch_fit(returns):
    """GARCH(1,1) by MLE with variance targeting (omega = var * (1 - alpha - beta)),
    optimised in an unconstrained (logit) space on percent returns so the
    simplex is well scaled. Returns parameters in return (not percent) units."""
    r = np.asarray(returns, dtype=float) * 100.0
    r = r - r.mean()
    var0 = float(np.var(r))

    def unpack(theta):
        alpha = 0.999 / (1.0 + math.exp(-theta[0]))
        beta = (0.999 - alpha) / (1.0 + math.exp(-theta[1]))
        return alpha, beta, var0 * (1.0 - alpha - beta)

    def nll(theta):
        alpha, beta, omega = unpack(theta)
        v = var0
        ll = 0.0
        for x in r:
            # v is the conditional variance FOR this observation; update after
            ll += math.log(v) + x * x / v
            v = omega + alpha * x * x + beta * v
        return 0.5 * ll

    best = None
    for a0, b0 in ((0.08, 0.90), (0.05, 0.93), (0.15, 0.80)):
        # invert the transform for the starting point
        ta = math.log(a0 / (0.999 - a0))
        tb = math.log((b0 / (0.999 - a0)) / (1.0 - b0 / (0.999 - a0)))
        res = minimize(nll, np.array([ta, tb]), method="Nelder-Mead",
                       options={"maxiter": 600, "xatol": 1e-6, "fatol": 1e-8})
        if best is None or res.fun < best.fun:
            best = res
    alpha, beta, omega = unpack(best.x)
    v = var0
    for x in r:
        v = omega + alpha * x * x + beta * v
    scale = 1e-4  # percent^2 -> return^2
    return {"omega": float(omega * scale), "alpha": float(alpha), "beta": float(beta),
            "lastVar": float(v * scale), "converged": bool(best.success)}


def garch_forecast(returns, horizon=21):
    r = np.asarray(returns, dtype=float)
    if len(r) < 100:
        raise ValueError(f"need at least 100 returns for GARCH, got {len(r)}")
    fit = garch_fit(r)
    omega, alpha, beta, v = fit["omega"], fit["alpha"], fit["beta"], fit["lastVar"]
    persistence = alpha + beta
    long_run = omega / max(1.0 - persistence, 1e-6)
    # average variance over the next `horizon` days from the recursion
    total = 0.0
    vh = v
    for _ in range(horizon):
        vh = omega + persistence * vh
        total += vh
    sigma = math.sqrt(total / horizon * TRADING_DAYS)
    return {"method": "garch", "sigma": sigma, "horizon": horizon, "nObs": len(r),
            "params": {k: fit[k] for k in ("omega", "alpha", "beta", "converged")},
            "longRunSigma": math.sqrt(long_run * TRADING_DAYS),
            "sigmaLo": None, "sigmaHi": None, "bandMethod": "n/a (MLE)"}


# ---- band + dispatcher ---------------------------------------------------------------------

def _error_band(returns, horizon, point_fn, max_points=60):
    """Walk the forecaster over history: forecast at t vs realized over
    [t, t+h). Band = exp(+/- 1 std of log forecast error)."""
    r = np.asarray(returns, dtype=float)
    errs = []
    start = max(MIN_OBS, len(r) - horizon - max_points * 5)
    for t in range(start, len(r) - horizon, 5):
        try:
            f = point_fn(r[:t])
        except Exception:
            continue
        realized = math.sqrt(np.mean(r[t:t + horizon] ** 2) * TRADING_DAYS)
        if f > 0 and realized > 0:
            errs.append(math.log(realized / f))
    if len(errs) < 5:
        return {"sigmaLo": None, "sigmaHi": None, "bandMethod": "insufficient history", "nBacktest": len(errs)}
    sd = float(np.std(errs))
    bias = float(np.mean(errs))
    return {"logErrorSd": sd, "logErrorBias": bias, "nBacktest": len(errs),
            "bandMethod": f"+/-1 sd of log forecast error over {len(errs)} rolling origins",
            "sigmaLo": None, "sigmaHi": None}  # filled by forecast() once sigma is known


def forecast(closes, horizon=21, method="har"):
    r = log_returns(closes)
    if len(r) < MIN_OBS:
        raise ValueError(f"need at least {MIN_OBS} daily closes, got {len(r) + 1}")
    if method == "har":
        try:
            out = har_forecast(r, horizon)
        except ValueError:
            out = ewma_forecast(r, horizon)
            out["fallbackFrom"] = "har"
    elif method == "ewma":
        out = ewma_forecast(r, horizon)
    elif method == "garch":
        out = garch_forecast(r, horizon)
    else:
        raise ValueError(f"unknown method {method!r}")
    sd = out.get("logErrorSd")
    if sd is not None:
        out["sigmaLo"] = out["sigma"] * math.exp(-sd)
        out["sigmaHi"] = out["sigma"] * math.exp(sd)
    out["realized21"] = realized_vol(r, 21)
    out["realized63"] = realized_vol(r, 63)
    return out


def vrp(iv_atm, sigma_forecast, history=None):
    """Volatility risk premium at one horizon, with a z-score against the
    symbol's own history when >= 20 observations exist."""
    if iv_atm is None or sigma_forecast is None:
        return {"vrp": None, "z": None, "n": 0}
    v = float(iv_atm) - float(sigma_forecast)
    out = {"vrp": round(v, 6), "ratio": round(float(iv_atm) / max(float(sigma_forecast), 1e-6), 4),
           "z": None, "n": 0}
    if history is not None:
        h = np.asarray([x for x in history if x is not None and np.isfinite(x)], dtype=float)
        out["n"] = int(len(h))
        if len(h) >= 20 and np.std(h) > 1e-9:
            out["z"] = round((v - float(np.mean(h))) / float(np.std(h)), 4)
            out["historyMean"] = round(float(np.mean(h)), 6)
    return out
