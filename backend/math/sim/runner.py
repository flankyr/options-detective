"""CLI entry `od-math sim` (one-shot, uses the data service so it stays out of
the warm pure-math engine).

stdin:
  {"cmd": "strategies"}
  {"cmd": "run", "strategy": "optimizer", "params": {...}, "config": {"symbol": "SPY",
   "start": "2026-01-02", "end": "2026-11-13", "initialCapital": 100000, ...}, "root"?: dir}
  {"cmd": "forecast_walkforward", "symbol": "SPY", "horizon": 21}
  {"cmd": "walkforward", "strategy": "strangle_16d", "grid": [{"delta": 0.16}, {"delta": 0.25}],
   "config": {...}, "trainDays": 250, "testDays": 60, "objective": "sharpe"}
Results are also saved under <root>/research/<timestamp>-<strategy>.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from data.base import Unsupported
from data.service import DataService
from sim.backtest import Backtester
from sim.strategies import registry
from sim.walkforward import forecaster_walkforward, walk_forward


def _save(root, name, result):
    d = os.path.join(root, "research")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(d, f"{stamp}-{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return path


def run_backtest(svc, strategy_name, params, config, log=None):
    reg = registry()
    if strategy_name not in reg:
        raise Unsupported(f"unknown strategy {strategy_name!r}; available: {', '.join(sorted(reg))}")
    strat = reg[strategy_name](**(params or {}))
    bt = Backtester(svc, strat, config, log=log)
    t0 = time.perf_counter()
    result = bt.run()
    result["elapsedSeconds"] = round(time.perf_counter() - t0, 2)
    return result


def handle(req, svc=None):
    if not isinstance(req, dict) or "cmd" not in req:
        return {"ok": False, "error": 'request must be {"cmd": "strategies|run|walkforward|forecast_walkforward", ...}'}
    svc = svc or DataService(root=req.get("root"))
    cmd = req["cmd"]
    try:
        if cmd == "strategies":
            result = [cls().describe() for cls in registry().values()]
        elif cmd == "run":
            result = run_backtest(svc, req.get("strategy", "optimizer"), req.get("params"), req.get("config"))
            result["savedTo"] = _save(svc.root, req.get("strategy", "optimizer"), result)
        elif cmd == "forecast_walkforward":
            hist = svc.underlying_history(req.get("symbol", "SPY"), days=int(req.get("days", 1500)))
            result = forecaster_walkforward([c for _, c in hist["rows"]], int(req.get("horizon", 21)))
            result["symbol"] = hist["symbol"]
        elif cmd == "walkforward":
            name = req.get("strategy", "strangle_16d")
            base = req.get("config") or {}
            probe = Backtester(svc, registry()[name](), base)
            dates = probe.dates()

            def run_fn(params, start, end):
                return run_backtest(svc, name, params, {**base, "start": start, "end": end})

            result = walk_forward(run_fn, req.get("grid") or [{}], dates, int(req.get("trainDays", 250)),
                                  int(req.get("testDays", 60)), req.get("objective", "sharpe"))
            result["savedTo"] = _save(svc.root, f"wf-{name}", result)
        elif cmd == "runs":
            d = os.path.join(svc.root, "research")
            files = sorted(os.listdir(d)) if os.path.isdir(d) else []
            result = []
            for f in files[-50:]:
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        r = json.load(fh)
                    result.append({"file": f, "strategy": (r.get("strategy") or {}).get("name"),
                                   "config": {k: r.get("config", {}).get(k) for k in ("symbol", "start", "end")},
                                   "metrics": r.get("metrics")})
                except Exception:
                    continue
        else:
            return {"ok": False, "error": f"unknown cmd {cmd!r}"}
    except Unsupported as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{cmd} failed: {type(exc).__name__}: {exc}"}
    return {"ok": True, "result": result}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        response = {"ok": False, "error": f"invalid JSON request: {exc}"}
    else:
        response = handle(req)
    json.dump(response, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
