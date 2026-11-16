"""Performance metrics on an equity curve and a trade list."""

from __future__ import annotations

import math

import numpy as np

TRADING_DAYS = 252


def summarize(curve, trades, initial):
    eq = np.array([c["equity"] for c in curve], dtype=float)
    out = {"days": len(eq), "trades": len(trades)}
    if len(eq) < 2:
        return {**out, "cagr": None, "sharpe": None, "sortino": None, "maxDrawdown": None, "calmar": None}
    rets = np.diff(eq) / eq[:-1]
    years = max(len(eq) - 1, 1) / TRADING_DAYS
    total = eq[-1] / eq[0] - 1.0
    cagr = (eq[-1] / eq[0]) ** (1.0 / years) - 1.0 if eq[0] > 0 and eq[-1] > 0 else None
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    sharpe = float(np.mean(rets) / sd * math.sqrt(TRADING_DAYS)) if sd > 1e-12 else None
    downside = rets[rets < 0]
    dsd = float(np.sqrt(np.mean(downside ** 2))) if len(downside) else 0.0
    sortino = float(np.mean(rets) / dsd * math.sqrt(TRADING_DAYS)) if dsd > 1e-12 else None
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = float(dd.min())
    calmar = (cagr / abs(max_dd)) if cagr is not None and max_dd < 0 else None
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    out.update({
        "totalReturn": round(total, 6),
        "cagr": None if cagr is None else round(cagr, 6),
        "annualVol": round(sd * math.sqrt(TRADING_DAYS), 6),
        "sharpe": None if sharpe is None else round(sharpe, 4),
        "sortino": None if sortino is None else round(sortino, 4),
        "maxDrawdown": round(max_dd, 6),
        "calmar": None if calmar is None else round(calmar, 4),
        "winRate": round(float(len(wins) / len(pnls)), 4) if len(pnls) else None,
        "profitFactor": (round(float(wins.sum() / -losses.sum()), 4) if len(losses) and losses.sum() < 0 else None),
        "avgWin": round(float(wins.mean()), 2) if len(wins) else None,
        "avgLoss": round(float(losses.mean()), 2) if len(losses) else None,
        "tailRatio": (round(float(np.percentile(pnls, 95) / -np.percentile(pnls, 5)), 4)
                      if len(pnls) >= 20 and np.percentile(pnls, 5) < 0 else None),
        "expectancy": round(float(pnls.mean()), 2) if len(pnls) else None,
        "totalCosts": round(float(sum(t["costs"] for t in trades)), 2),
        "avgDaysHeld": round(float(np.mean([t["daysHeld"] for t in trades])), 1) if trades else None,
        "turnover": round(float(sum(abs(t["entryCost"]) * t["contracts"] for t in trades) / initial / years), 4) if trades else 0.0,
        "sharpeCI": sharpe_ci(rets),
        "finalEquity": round(float(eq[-1]), 2),
    })
    return out


def sharpe_ci(rets, n_boot=400, seed=0, level=0.90):
    """Bootstrap confidence interval for the annualised Sharpe ratio.
    Daily returns are resampled with replacement in blocks of 5 to keep some
    serial dependence. Returns [lo, hi] or None."""
    rets = np.asarray(rets, dtype=float)
    if len(rets) < 40:
        return None
    rng = np.random.default_rng(seed)
    block = 5
    n_blocks = len(rets) // block
    blocks = rets[: n_blocks * block].reshape(n_blocks, block)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, n_blocks, n_blocks)
        sample = blocks[idx].ravel()
        sd = sample.std(ddof=1)
        if sd > 0:
            vals.append(sample.mean() / sd * math.sqrt(TRADING_DAYS))
    if len(vals) < 50:
        return None
    lo, hi = np.percentile(vals, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return [round(float(lo), 4), round(float(hi), 4)]
