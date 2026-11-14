"""CLI entry `od-math data`: the Node data layer's single door to market data.

stdin: {"cmd": "chain", "symbol": "SPY", "asof"?: "YYYY-MM-DD", "provider"?: name,
        "max_expirations"?: 6, "min_dte"?: 1, "max_dte"?: 120}
       {"cmd": "history", "symbol": "SPY", "days"?: 400}
       {"cmd": "forecast", "symbol": "SPY", "horizon"?: 21, "method"?: "har|ewma|garch", "iv_atm"?: 0.18}
       {"cmd": "status"}
       {"cmd": "config", "patch": {"massiveApiKey": "...", "liveOrder": [...]}}
       {"cmd": "rates", "force"?: true}
stdout: {"ok": true, "result": ...} | {"ok": false, "error": "..."}; exit 0 always.
"""

from __future__ import annotations

import json
import sys

from data.base import Unsupported
from data.service import DataService


def handle(req: dict, service: DataService | None = None) -> dict:
    if not isinstance(req, dict) or "cmd" not in req:
        return {"ok": False, "error": 'request must be {"cmd": "chain|status|config|rates", ...}'}
    svc = service or DataService(root=req.get("root"))
    cmd = req["cmd"]
    try:
        if cmd == "chain":
            symbol = req.get("symbol")
            if not symbol or not isinstance(symbol, str):
                return {"ok": False, "error": "symbol is required"}
            result = svc.get_chain(symbol.strip(), req.get("asof"),
                                   max_expirations=int(req.get("max_expirations", 6)),
                                   min_dte=int(req.get("min_dte", 1)),
                                   max_dte=int(req.get("max_dte", 120)),
                                   provider=req.get("provider"))
        elif cmd == "history":
            result = svc.underlying_history(str(req.get("symbol", "")).strip(), int(req.get("days", 400)))
        elif cmd == "forecast":
            result = svc.forecast(str(req.get("symbol", "")).strip(), int(req.get("horizon", 21)),
                                  req.get("method", "har"), req.get("iv_atm"))
        elif cmd == "status":
            result = svc.status()
        elif cmd == "config":
            result = svc.set_config(req.get("patch") or {})
        elif cmd == "rates":
            svc.rates.refresh(force=bool(req.get("force")))
            result = svc.rates.describe()
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
