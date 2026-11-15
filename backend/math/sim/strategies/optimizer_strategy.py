"""The app's own optimizer as a strategy: screen, take the top K by score."""

from __future__ import annotations

import optimizer
from sim.strategy import Strategy


class OptimizerStrategy(Strategy):
    """Runs optimizer.screen() in the chosen mode and opens the top `topK`
    candidates with the optimizer's own sizing, at most `maxOpen` positions."""

    name = "optimizer"
    params = {
        "mode": "both", "view": "neutral", "conviction": 0.5, "topK": 1, "maxOpen": 3,
        "minDTE": 20, "maxDTE": 60, "definedRiskOnly": False, "allowedStrategies": [],
        "profitTarget": 0.5, "stopMultiple": 2.0, "exitDte": 7, "debitStopPct": 0.5,
        "riskTolerancePct": 2.0, "kellyFraction": 0.25,
    }

    def entries(self, ctx):
        if len(ctx.open_positions) >= self.p["maxOpen"]:
            return []
        options = {
            "mode": self.p["mode"], "view": self.p["view"], "conviction": self.p["conviction"],
            "capital": ctx.account.equity, "riskTolerancePct": self.p["riskTolerancePct"],
            "kellyFraction": self.p["kellyFraction"], "minDTE": self.p["minDTE"], "maxDTE": self.p["maxDTE"],
            "definedRiskOnly": self.p["definedRiskOnly"], "allowedStrategies": self.p["allowedStrategies"],
            "topN": max(self.p["topK"] * 5, 10),
        }
        res = optimizer.screen(ctx.payload, options, ctx.forecast)
        held = {(p.structure, p.expiration) for p in ctx.open_positions}
        picks = []
        for c in res["candidates"]:
            if c["sizing"]["contractsSuggested"] <= 0:
                continue
            if (c["strategyType"], c["expiration"]) in held:
                continue
            picks.append(c)
            if len(picks) >= self.p["topK"]:
                break
        return picks
