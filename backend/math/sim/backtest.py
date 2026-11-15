"""Point-in-time backtester."""

from __future__ import annotations

import math
from datetime import date

import numpy as np

import bsm
import costs as costmodel
import distribution as D
from data.base import Unsupported
from sim.strategy import Account, Context, Position

MULT = 100


class Backtester:
    def __init__(self, data_service, strategy, config=None, log=None):
        self.svc = data_service
        self.strategy = strategy
        self.cfg = {
            "symbol": "SPY", "start": None, "end": None, "initialCapital": 100_000.0,
            "screenEvery": 5,            # trading days between screens (marks run daily)
            "forecastMethod": "har", "forecastHorizon": 30, "useForecast": True,
            "maxExpirations": 8, "minDTE": 1, "maxDTE": 120, "costs": {},
            "skipMissingDays": True,
            **(config or {}),
        }
        self.log = log or (lambda *a: None)
        self.account = Account(self.cfg["initialCapital"], self.cfg["initialCapital"], self.cfg["initialCapital"])
        self.open: list[Position] = []
        self.closed: list[dict] = []
        self.curve: list[dict] = []
        self.skipped: list[dict] = []
        self._seq = 0

    # ---- data ------------------------------------------------------------------------
    def dates(self):
        sym = self.cfg["symbol"]
        start, end = self.cfg["start"], self.cfg["end"]
        arch = self.svc.providers.get("archive")
        ds = arch.available_dates(sym) if arch else []
        if not ds:
            import mcal
            d0 = date.fromisoformat(start)
            d1 = date.fromisoformat(end)
            ds = []
            d = d0
            while d <= d1:
                if mcal.is_trading_day(d):
                    ds.append(d.isoformat())
                d = date.fromordinal(d.toordinal() + 1)
        return [d for d in ds if (not start or d >= start) and (not end or d <= end)]

    def chain(self, asof, enrich=True):
        return self.svc.get_chain(self.cfg["symbol"], asof, max_expirations=self.cfg["maxExpirations"],
                                  min_dte=self.cfg["minDTE"], max_dte=self.cfg["maxDTE"], enrich=enrich)

    def forecast(self, asof, payload):
        if not self.cfg["useForecast"]:
            return None
        try:
            return self.svc.forecast(self.cfg["symbol"], self.cfg["forecastHorizon"], self.cfg["forecastMethod"],
                                     payload.get("atmIv30") or payload.get("atmIvModel"), asof)
        except Exception as exc:
            self.log(f"{asof}: no forecast ({exc})")
            return None

    # ---- marking -----------------------------------------------------------------------
    @staticmethod
    def _lookup(payload, expiration, strike, is_call):
        chain = payload["chains"].get(expiration)
        if not chain:
            return None
        for c in chain["calls" if is_call else "puts"]:
            if abs(c["strike"] - strike) < 1e-9:
                return c
        return None

    def leg_value(self, leg, payload, asof):
        S = float(payload["price"])
        if leg["type"].endswith("stock"):
            return S
        exp = leg.get("expiration")
        K = float(leg["strike"])
        is_call = leg["type"].endswith("call")
        if exp and exp <= asof:
            return max(S - K, 0.0) if is_call else max(K - S, 0.0)   # settled at the close
        c = self._lookup(payload, exp, K, is_call) if exp else None
        if c and c.get("mid"):
            return float(c["mid"])
        # not in today's chain (expiry dropped out of the window, strike missing):
        # BSM at the smile vol for that expiry, else the leg's own entry vol
        T = max((date.fromisoformat(exp) - date.fromisoformat(asof)).days, 0) / 365.0 if exp else 0.0
        r = float((payload.get("rates") or {}).get(exp, 0.04))
        q = float(payload.get("dividendYield") or 0.0)
        vol = None
        smile = (payload.get("smiles") or {}).get(exp)
        if smile:
            from smile import Smile
            vol = float(Smile.from_dict(smile).iv_strike(K, smile["forward"]))
        vol = vol or leg.get("iv") or payload.get("atmIvModel") or 0.25
        return float(bsm.price(S, K, T, r, q, vol, is_call))

    def unit_value(self, pos, payload, asof):
        total = 0.0
        for leg in pos.legs:
            sign = 1.0 if leg["type"].startswith("long") else -1.0
            scale = leg["qty"] if leg["type"].endswith("stock") else leg["qty"] * MULT
            total += sign * self.leg_value(leg, payload, asof) * scale
        return total

    def mark_all(self, payload, asof):
        for pos in self.open:
            pos.mark = self.unit_value(pos, payload, asof)
            pos.pnl = (pos.mark - pos.entry_cost) * pos.contracts - pos.entry_costs_paid
            pos.mae = min(pos.mae, pos.pnl)
            pos.mfe = max(pos.mfe, pos.pnl)
            pos.days_held += 1
        self.account.equity = self.account.cash + sum(p.mark * p.contracts for p in self.open)

    # ---- trades -------------------------------------------------------------------------
    def open_position(self, cand, asof, payload):
        n = int(cand["sizing"]["contractsSuggested"])
        if n <= 0:
            return None
        legs = []
        for l in cand["legs"]:
            leg = dict(l)
            leg.setdefault("expiration", cand["expiration"])
            legs.append(leg)
        unit_cost = D.entry_cost(legs)
        one_way = costmodel.position_costs(legs, cfg={**self.cfg["costs"], "roundTrip": False})["total"]
        entry_costs = one_way * n
        if unit_cost * n + entry_costs > self.account.cash and unit_cost > 0:
            return None  # cannot afford the debit
        self._seq += 1
        ev = cand.get("evaluation") or {}
        pos = Position(
            id=f"{self._seq}:{cand['strategyType']}:{asof}", strategy=self.strategy.name,
            symbol=cand["symbol"], opened=asof, expiration=cand["expiration"], legs=legs, contracts=n,
            entry_cost=unit_cost, entry_costs_paid=entry_costs,
            predicted=(ev.get("scenario") or ev.get("market") or {}),
            predicted_market=(ev.get("market") or {}), structure=cand["strategyType"],
            meta={"compositeScore": cand.get("compositeScore"), "mode": (cand.get("meta") or {}).get("mode"),
                  "spot": payload["price"], "binding": cand["sizing"].get("binding")},
        )
        pos.mark = unit_cost
        pos.pnl = -entry_costs
        pos.mae = pos.pnl
        pos.mfe = pos.pnl
        self.account.cash -= unit_cost * n + entry_costs
        self.open.append(pos)
        return pos

    def close_position(self, pos, asof, payload, reason):
        exit_costs = 0.0
        if pos.expiration > asof:  # closing in the market: pay the exit half-spreads
            exit_costs = costmodel.position_costs(pos.legs, cfg={**self.cfg["costs"], "roundTrip": False})["total"] * pos.contracts
        proceeds = pos.mark * pos.contracts - exit_costs
        self.account.cash += proceeds
        realized = proceeds - pos.entry_cost * pos.contracts - pos.entry_costs_paid
        self.open.remove(pos)
        pm = pos.predicted or {}
        self.closed.append({
            "id": pos.id, "structure": pos.structure, "symbol": pos.symbol, "opened": pos.opened,
            "closed": asof, "expiration": pos.expiration, "reason": reason, "contracts": pos.contracts,
            "entryCost": round(pos.entry_cost, 2), "exitValue": round(pos.mark, 2),
            "costs": round(pos.entry_costs_paid + exit_costs, 2), "pnl": round(realized, 2),
            "pnlPerUnit": round(realized / pos.contracts, 2), "daysHeld": pos.days_held,
            "mae": round(pos.mae, 2), "mfe": round(pos.mfe, 2),
            "predictedPop": pm.get("pop"), "predictedEv": pm.get("ev"), "predictedCvar": pm.get("cvar05"),
            "predictedPopMarket": (pos.predicted_market or {}).get("pop"),
            "predictedEvMarket": (pos.predicted_market or {}).get("ev"),
            "capitalRequired": None, "meta": pos.meta,
        })
        self.account.equity = self.account.cash + sum(p.mark * p.contracts for p in self.open)

    # ---- main loop ------------------------------------------------------------------------
    def run(self):
        dates = self.dates()
        if not dates:
            raise Unsupported(f"no archived dates for {self.cfg['symbol']} in the requested range")
        since_screen = self.cfg["screenEvery"]
        for i, asof in enumerate(dates):
            screen_day = since_screen + 1 >= self.cfg["screenEvery"] and i < len(dates) - 1
            try:
                payload = self.chain(asof, enrich=screen_day)  # smiles only needed to screen
            except Exception as exc:
                self.skipped.append({"date": asof, "error": str(exc)})
                if not self.cfg["skipMissingDays"]:
                    raise
                continue
            self.mark_all(payload, asof)
            ctx = Context(asof, payload, None, list(self.open), self.account, self.cfg)
            # management first: a position closed today frees the slot for today's screen
            for pos in list(self.open):
                action = self.strategy.manage(pos, ctx)
                if action in ("close", "roll"):
                    self.close_position(pos, asof, payload, "expired" if pos.expiration <= asof else action)
            since_screen += 1
            if screen_day:
                since_screen = 0
                fc = self.forecast(asof, payload)
                ctx = Context(asof, payload, fc, list(self.open), self.account, self.cfg)
                try:
                    picks = self.strategy.entries(ctx)
                except Exception as exc:
                    self.log(f"{asof}: strategy error {exc}")
                    picks = []
                for cand in picks:
                    self.open_position(cand, asof, payload)
            self.account.equity = self.account.cash + sum(p.mark * p.contracts for p in self.open)
            self.curve.append({"date": asof, "equity": round(self.account.equity, 2), "cash": round(self.account.cash, 2),
                               "open": len(self.open), "spot": payload["price"]})
            self.log(f"{asof}: equity {self.account.equity:,.0f} open {len(self.open)}")
        if not self.curve:
            first = self.skipped[0]["error"] if self.skipped else "no data"
            raise Unsupported(f"no chain could be loaded for any of {len(dates)} dates: {first}")
        # liquidate at the last mark so the curve is comparable
        if self.open and self.curve:
            last = self.curve[-1]["date"]
            payload = self.chain(last, enrich=False)
            for pos in list(self.open):
                self.close_position(pos, last, payload, "end")
            self.curve[-1]["equity"] = round(self.account.equity, 2)
        return self.result()

    def result(self):
        from sim.metrics import summarize
        from sim.calibration import calibrate
        return {
            "strategy": self.strategy.describe(),
            "config": {k: v for k, v in self.cfg.items()},
            "curve": self.curve,
            "trades": self.closed,
            "skippedDays": self.skipped,
            "metrics": summarize(self.curve, self.closed, self.cfg["initialCapital"]),
            "calibration": calibrate(self.closed),
        }
