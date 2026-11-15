"""Reference strategies: fixed rules with no model, the yardsticks the
optimizer has to beat. Each uses the same optimizer plumbing for pricing and
sizing but forces one structure and one delta."""

from __future__ import annotations

import optimizer
from sim.strategy import Strategy


def _screen_fixed(ctx, structure, delta, p, extra=None):
    options = {
        "mode": "market", "view": "neutral", "capital": ctx.account.equity,
        "riskTolerancePct": p["riskTolerancePct"], "minDTE": p["minDTE"], "maxDTE": p["maxDTE"],
        "allowedStrategies": [structure], "topN": 100, **(extra or {}),
    }
    res = optimizer.screen(ctx.payload, options, None)
    cands = [c for c in res["candidates"] if c["sizing"]["contractsSuggested"] > 0
             and abs(abs(c["meta"]["params"].get("delta", delta)) - delta) < 1e-9]
    if not cands:
        cands = [c for c in res["candidates"] if c["sizing"]["contractsSuggested"] > 0]
    # nearest expiry inside the window, then the tightest wings
    cands.sort(key=lambda c: (c["daysToExpiry"], c["meta"]["params"].get("width", 0)))
    return cands[:1]


class MonthlyStrangle(Strategy):
    """Sell a 16-delta strangle 30-60 DTE whenever none is open; 50% profit
    target, 2x credit stop, close at 7 DTE. The classic premium-selling baseline."""

    name = "strangle_16d"
    params = {"delta": 0.16, "minDTE": 30, "maxDTE": 60, "profitTarget": 0.5, "stopMultiple": 2.0,
              "exitDte": 7, "riskTolerancePct": 2.0}

    def entries(self, ctx):
        if ctx.open_positions:
            return []
        return _screen_fixed(ctx, "short_strangle", self.p["delta"], self.p)


class PutSpreadWhenRich(Strategy):
    """Sell a 30-delta put credit spread only when IV is rich versus the
    forecast (VRP ratio above `minRatio`); otherwise stay flat."""

    name = "put_spread_vrp"
    params = {"delta": 0.30, "minRatio": 1.10, "minDTE": 30, "maxDTE": 60, "profitTarget": 0.5,
              "stopMultiple": 2.0, "exitDte": 7, "riskTolerancePct": 2.0}

    def entries(self, ctx):
        if ctx.open_positions or not ctx.forecast or not ctx.forecast.get("sigma"):
            return []
        iv = ctx.payload.get("atmIv30") or ctx.payload.get("atmIvModel")
        if not iv or iv / ctx.forecast["sigma"] < self.p["minRatio"]:
            return []
        return _screen_fixed(ctx, "put_credit_spread", self.p["delta"], self.p, {"definedRiskOnly": True})


class BuyAndHold(Strategy):
    """Hold 100 shares per `lots` from the first day. The do-nothing benchmark."""

    name = "buy_and_hold"
    params = {"lots": 1}

    def entries(self, ctx):
        if ctx.open_positions:
            return []
        spot = float(ctx.payload["price"])
        return [{
            "strategyType": "buy_and_hold", "structure": "buy_and_hold", "symbol": ctx.payload["symbol"],
            "expiration": "2099-12-31", "legs": [{"type": "long_stock", "price": spot, "qty": 100}],
            "sizing": {"contractsSuggested": self.p["lots"], "capitalRequired": spot * 100},
            "evaluation": {"market": {"pop": None, "ev": None, "cvar05": None}, "scenario": None},
            "costs": {"total": 0.0}, "meta": {},
        }]

    def manage(self, position, ctx):
        return "hold"
