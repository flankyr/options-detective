"""Walk-forward evaluation.

1. forecaster_walkforward: which realized-vol forecaster predicts best out of
   sample? For each origin t (every `step` days) fit on history up to t,
   forecast `horizon`, compare with realized vol over (t, t+horizon].
2. walk_forward: generic parameter selection. Split the dates into rolling
   (train, test) windows; on each train window run `run_fn(params, dates)`
   for every candidate in `grid`, pick the best by `objective`, run it on the
   test window, stitch the test results. The stitched curve is the honest
   out-of-sample record of "pick the best recent parameter".
"""

from __future__ import annotations

import math

import numpy as np

import volforecast as V


def forecaster_walkforward(closes, horizon=21, step=5, methods=("har", "ewma", "garch"), min_obs=250):
    r = V.log_returns(closes)
    out = {m: {"errors": [], "bias": []} for m in methods}
    for t in range(min_obs, len(r) - horizon, step):
        realized = math.sqrt(np.mean(r[t:t + horizon] ** 2) * V.TRADING_DAYS)
        for m in methods:
            try:
                f = V.forecast(np.exp(np.cumsum(np.concatenate([[0.0], r[:t]]))) * 100.0, horizon, m)
            except Exception:
                continue
            if f["sigma"] > 0 and realized > 0:
                e = math.log(realized / f["sigma"])
                out[m]["errors"].append(e)
                out[m]["bias"].append(e)
    summary = {}
    for m, d in out.items():
        e = np.array(d["errors"])
        summary[m] = {"n": int(len(e)), "rmseLog": round(float(np.sqrt(np.mean(e ** 2))), 4) if len(e) else None,
                      "biasLog": round(float(np.mean(e)), 4) if len(e) else None,
                      "hitWithin20pct": round(float(np.mean(np.abs(e) < math.log(1.2))), 4) if len(e) else None}
    best = min((m for m in summary if summary[m]["rmseLog"] is not None), key=lambda m: summary[m]["rmseLog"], default=None)
    return {"horizon": horizon, "origins": max((v["n"] for v in summary.values()), default=0), "methods": summary, "best": best}


def walk_forward(run_fn, grid, dates, train_days=250, test_days=60, objective="sharpe"):
    """run_fn(params, start, end) -> result dict with ['metrics'][objective] and ['curve'].
    Returns per-window choices and the stitched out-of-sample curve."""
    windows = []
    i = 0
    while i + train_days + test_days <= len(dates):
        train = (dates[i], dates[i + train_days - 1])
        test = (dates[i + train_days], dates[min(i + train_days + test_days - 1, len(dates) - 1)])
        windows.append((train, test))
        i += test_days
    chosen = []
    stitched = []
    for train, test in windows:
        best, best_val = None, -math.inf
        for params in grid:
            res = run_fn(params, train[0], train[1])
            val = (res.get("metrics") or {}).get(objective)
            if val is not None and val > best_val:
                best, best_val = params, val
        if best is None:
            continue
        test_res = run_fn(best, test[0], test[1])
        chosen.append({"train": train, "test": test, "params": best, "trainObjective": round(best_val, 4),
                       "testMetrics": test_res.get("metrics")})
        stitched.extend(test_res.get("curve", []))
    return {"windows": chosen, "stitchedCurve": stitched, "objective": objective}
