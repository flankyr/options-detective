"""Were the predictions right? Reliability of POP and EV at entry.

For every closed trade the backtester stores the POP and EV the model
predicted at entry (ranking distribution, and the market density as a
control). Calibration groups trades into POP buckets and compares the
predicted frequency with the realized win rate; the Brier score summarises
it (0 = perfect, 0.25 = coin flip). For EV it regresses realized P&L per unit
on predicted EV per unit: slope ~1 and intercept ~0 means the EV scale is
believable, slope ~0 means the model's edge did not show up.
"""

from __future__ import annotations

import numpy as np

BUCKETS = [(0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def _reliability(pops, wins):
    rows = []
    for lo, hi in BUCKETS:
        m = (pops >= lo) & (pops < hi)
        n = int(m.sum())
        if n == 0:
            continue
        rows.append({"bucket": f"{int(lo*100)}-{int(min(hi,1.0)*100)}%", "n": n,
                     "predicted": round(float(pops[m].mean()), 4), "realized": round(float(wins[m].mean()), 4)})
    brier = float(np.mean((pops - wins) ** 2)) if len(pops) else None
    return {"buckets": rows, "brier": None if brier is None else round(brier, 4), "n": int(len(pops))}


def calibrate(trades):
    tr = [t for t in trades if t.get("predictedPop") is not None]
    out = {"n": len(tr)}
    if not tr:
        return {**out, "pop": None, "popMarket": None, "ev": None}
    wins = np.array([1.0 if t["pnl"] > 0 else 0.0 for t in tr])
    pops = np.array([float(t["predictedPop"]) for t in tr])
    out["pop"] = _reliability(pops, wins)
    mk = [t for t in tr if t.get("predictedPopMarket") is not None]
    if mk:
        out["popMarket"] = _reliability(np.array([float(t["predictedPopMarket"]) for t in mk]),
                                        np.array([1.0 if t["pnl"] > 0 else 0.0 for t in mk]))
    else:
        out["popMarket"] = None
    ev_tr = [t for t in tr if t.get("predictedEv") is not None]
    if len(ev_tr) >= 5:
        x = np.array([float(t["predictedEv"]) for t in ev_tr])
        y = np.array([float(t["pnlPerUnit"]) for t in ev_tr])
        if np.std(x) > 1e-9:
            slope, intercept = np.polyfit(x, y, 1)
            resid = y - (slope * x + intercept)
            r2 = 1 - resid.var() / y.var() if y.var() > 0 else None
        else:
            slope, intercept, r2 = None, None, None
        out["ev"] = {"n": len(ev_tr), "meanPredicted": round(float(x.mean()), 2), "meanRealized": round(float(y.mean()), 2),
                     "bias": round(float(y.mean() - x.mean()), 2),
                     "slope": None if slope is None else round(float(slope), 4),
                     "intercept": None if intercept is None else round(float(intercept), 2),
                     "r2": None if r2 is None else round(float(r2), 4)}
    else:
        out["ev"] = None
    return out
