"""Strategy plug-in API.

Subclass Strategy, drop the module in sim/strategies/, and the Research tab
can run it. The contract is small on purpose:

    class MyStrategy(Strategy):
        name = "my_strategy"
        params = {"delta": 0.16}            # defaults, overridable per run

        def entries(self, ctx) -> list[dict]:
            # ctx.payload: enriched chain for ctx.asof (schema v2 + smiles)
            # ctx.forecast: realized-vol forecast dict or None
            # ctx.open_positions: list of Position
            # ctx.account: Account (equity, cash)
            # return candidate dicts in the optimizer's shape (legs, sizing, ...)

        def manage(self, position, ctx) -> str:
            # "hold" | "close" | "roll"

Helpers for common rules (profit target, stop, DTE exit) live here so
reference strategies and the optimizer strategy share one definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Account:
    equity: float
    cash: float
    initial: float


@dataclass
class Position:
    id: str
    strategy: str
    symbol: str
    opened: str
    expiration: str
    legs: list
    contracts: int
    entry_cost: float            # per unit, + debit / - credit
    entry_costs_paid: float      # transaction costs at entry (total $)
    predicted: dict              # pop/ev/cvar at entry (ranking distribution)
    predicted_market: dict       # same under the market density
    structure: str
    mark: float = 0.0            # current value per unit (+ = asset)
    pnl: float = 0.0             # unrealized $ incl. entry costs
    mae: float = 0.0             # worst unrealized P&L seen
    mfe: float = 0.0             # best unrealized P&L seen
    days_held: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Context:
    asof: str
    payload: dict
    forecast: dict | None
    open_positions: list
    account: Account
    config: dict


class Strategy:
    name = "base"
    params: dict = {}

    def __init__(self, **overrides):
        self.p = {**self.params, **overrides}

    def entries(self, ctx: Context) -> list:
        return []

    def manage(self, position: Position, ctx: Context) -> str:
        return default_manage(position, ctx, self.p)

    def describe(self) -> dict:
        return {"name": self.name, "params": self.p, "doc": (self.__doc__ or "").strip()}


def default_manage(position: Position, ctx: Context, p: dict) -> str:
    """Shared rules: profit target (fraction of max credit, credit trades),
    stop (multiple of credit, credit trades), fixed DTE exit, expiry."""
    dte = _dte(position.expiration, ctx.asof)
    if dte <= 0:
        return "close"
    exit_dte = p.get("exitDte")
    if exit_dte is not None and dte <= exit_dte:
        return "close"
    credit = -position.entry_cost if position.entry_cost < 0 else None
    if credit:
        gain_per_unit = position.pnl / max(position.contracts, 1)  # excludes future exit costs
        target = p.get("profitTarget")
        if target is not None and gain_per_unit >= target * credit:
            return "close"
        stop = p.get("stopMultiple")
        if stop is not None and gain_per_unit <= -stop * credit:
            return "close"
    else:
        stop_pct = p.get("debitStopPct")
        if stop_pct is not None and position.contracts and position.entry_cost > 0:
            if position.pnl / position.contracts <= -stop_pct * position.entry_cost:
                return "close"
    return "hold"


def _dte(expiration: str, asof: str) -> int:
    from datetime import date
    return (date.fromisoformat(expiration) - date.fromisoformat(asof)).days
