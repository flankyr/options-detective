"""Strategy optimizer: the v2 screen (Phase 4).

screen(payload, options, forecast) -> ranked candidates.

Pipeline, per expiry:
  Book (liquid contracts, smile vol, delta)
  -> structures x search grid -> legs (deduplicated)
  -> market density (Breeden-Litzenberger on this expiry's smile)
  -> scenario density (forecast vol / view tilt) when the mode asks for it
  -> position_metrics under both, costs from the cost model
  -> greeks (BSM, dividend yield, expiry rate), exact payoff summary
  -> size-invariant score, sizing v2, rationale

Score v2 (0-10), every component in [0, 1] and independent of contract count:
  evCvar 0.40   EV / |CVaR 5%| under the ranking distribution, after costs,
                mapped linearly from [-0.5, +0.5] (EV of half the tail loss)
  pop 0.15      probability of profit under the ranking distribution
  theta 0.15    annualised net theta per dollar of capital at risk, [-1, 1] -> [0, 1]
  liquidity 0.15 spread tightness, open interest vs size, volume
  confidence 0.15 smile RMSE, forecast band, data freshness

The ranking distribution is the scenario density when mode != market,
otherwise the market density (Transparent Analyzer: EV <= 0 everywhere and
the score degrades to liquidity + confidence + theta).
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np

import bsm
import costs as costmodel
import distribution as D
import payoff as payoff_mod
import structures as ST
import volmodel
from smile import Smile

WEIGHTS = {"evCvar": 0.40, "pop": 0.15, "theta": 0.15, "liquidity": 0.15, "confidence": 0.15}

DEFAULTS = {
    "mode": "both",               # market | vrp | directional | both
    "view": "neutral",            # bullish | bearish | neutral | income
    "conviction": 0.5,
    "capital": 25_000.0,
    "riskTolerancePct": 2.0,      # stress-loss budget per trade, % of capital
    "maxLossDollars": None,
    "maxBuyingPowerPct": 50.0,    # cap on capital tied up in one trade
    "kellyFraction": 0.25,        # fraction of full Kelly
    "stressSigmas": 2.0,
    "minDTE": 5,
    "maxDTE": 90,
    "definedRiskOnly": False,
    "allowedStrategies": [],
    "topN": 20,
    "costs": {},
}

# v1 strategy names that map onto several v2 structures
V1_ALIASES = {
    "call_vertical": ["call_vertical", "put_credit_spread"],
    "put_vertical": ["put_vertical", "call_credit_spread"],
}


def _sign(x, tol=0.0):
    return 0 if x is None or abs(x) <= tol else (1 if x > 0 else -1)


def regime(payload, forecast, opts):
    """VRP sign (+1 rich, -1 cheap, 0 unknown/flat) and view sign."""
    vrp_sign = 0
    vrp_info = None
    if forecast and forecast.get("sigma") and payload.get("atmIvModel"):
        iv = float(payload.get("atmIv30") or payload["atmIvModel"])
        sig = float(forecast["sigma"])
        v = forecast.get("vrp") or {}
        z = v.get("z")
        # with a z-score, rich/cheap means |z| >= 0.5; without one, use the
        # raw ratio with a 10% dead band so a hair of difference is "flat"
        if z is not None:
            vrp_sign = _sign(z, 0.5)
        else:
            vrp_sign = _sign(iv / sig - 1.0, 0.10)
        vrp_info = {"ivAtm": iv, "sigmaForecast": sig, "vrp": iv - sig, "ratio": iv / sig,
                    "z": z, "method": forecast.get("method"), "sign": vrp_sign}
    view = opts["view"]
    view_sign = {"bullish": 1, "bearish": -1}.get(view, 0)
    return vrp_sign, view_sign, view == "income", vrp_info


def eligible_structures(opts, vrp_sign, view_sign, income):
    allowed = set()
    for name in opts.get("allowedStrategies") or []:
        allowed.update(V1_ALIASES.get(name, [name]))
    out = []
    for s in ST.ALL_STRUCTURES:
        if allowed and s not in allowed:
            continue
        if opts["definedRiskOnly"] and ST.SPEC[s]["risk"] == "undefined":
            continue
        if ST.eligible(s, vrp_sign=vrp_sign, view_sign=view_sign, income=income):
            out.append(s)
    if not out:  # conflicting regime: screen everything, the score decides
        out = [s for s in ST.ALL_STRUCTURES if not (opts["definedRiskOnly"] and ST.SPEC[s]["risk"] == "undefined")
               and (not allowed or s in allowed)]
    return out


# ---- per-candidate numbers ---------------------------------------------------------------

def leg_greeks(legs, book: ST.Book):
    out = []
    for leg in legs:
        if leg["type"].endswith("stock"):
            out.append(None)
            continue
        T = leg.get("T", book.T)
        g = bsm.greeks(book.spot, leg["strike"], T, book.r, book.q, leg["iv"] or book.atm_iv,
                       leg["type"].endswith("call"))
        out.append({k: float(v) for k, v in g.items()})
    return out


def net_greeks(legs, greeks):
    net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for leg, g in zip(legs, greeks):
        sign = 1.0 if leg["type"].startswith("long") else -1.0
        if leg["type"].endswith("stock"):
            net["delta"] += sign * leg["qty"]
            continue
        for k in net:
            net[k] += sign * leg["qty"] * 100 * g[k]
    return {k: round(v, 4) for k, v in net.items()}


def liquidity_of(legs):
    opt = [l for l in legs if not l["type"].endswith("stock")]
    spreads = [l["spreadPct"] for l in opt if l.get("spreadPct") is not None]
    vols = [l["volume"] for l in opt if l.get("volume") is not None]
    ois = [l["openInterest"] for l in opt if l.get("openInterest") is not None]
    return {
        "bidAskSpread": round(sum(spreads) / len(spreads), 4) if spreads else None,
        "volume": min(vols) if vols else None,
        "openInterest": min(ois) if ois else None,
        "unknown": any(l.get("liquidityUnknown") for l in opt),
    }


def capital_requirement(structure, legs, summary, book: ST.Book):
    """Buying power for one unit: cash-secured / Reg-T style, labelled."""
    debit = D.entry_cost(legs)
    max_loss = summary.get("max_loss")
    if structure == "cash_secured_put":
        k = legs[0]["strike"]
        return {"amount": k * 100 + debit, "basis": "cash-secured (strike x 100 less credit)"}
    if structure == "covered_call":
        return {"amount": debit, "basis": "stock cost less premium"}
    if max_loss is not None and math.isfinite(max_loss):
        return {"amount": max(max_loss, debit if debit > 0 else 0.0), "basis": "defined risk (max loss)"}
    # undefined risk: Reg-T naked margin per short leg
    S = book.spot
    total = 0.0
    for leg in legs:
        if not leg["type"].startswith("short") or leg["type"].endswith("stock"):
            continue
        k, prem, qty = leg["strike"], leg["price"], leg["qty"]
        otm = max(k - S, 0.0) if leg["type"].endswith("call") else max(S - k, 0.0)
        total += max(0.20 * S - otm + prem, 0.10 * k + prem) * 100 * qty
    long_cost = sum(l["price"] * 100 * l["qty"] for l in legs if l["type"].startswith("long") and not l["type"].endswith("stock"))
    return {"amount": total + long_cost, "basis": "Reg-T naked margin estimate"}


def stress_loss(legs, book: ST.Book, sigmas):
    """Worst P&L for one unit at the horizon under a +/- `sigmas` move (or at the
    grid edges for defined risk). Replaces 'stock to zero' as the risk unit."""
    move = book.atm_iv * math.sqrt(book.T) * sigmas
    xs = np.array([book.spot * math.exp(-move), book.spot * math.exp(move), book.spot])
    pnl = D.payoff_grid(legs, xs)
    return float(-min(pnl.min(), 0.0))


def size(structure, legs, metrics, cap_req, stress, opts, mode):
    """contracts = min(risk budget / stress loss, buying power cap / capital,
    fractional Kelly on the ranking distribution when there is a model edge)."""
    capital = float(opts["capital"])
    budget = capital * float(opts["riskTolerancePct"]) / 100.0
    if opts.get("maxLossDollars"):
        budget = min(budget, float(opts["maxLossDollars"]))
    by_risk = math.floor(budget / stress) if stress > 0 else 0
    bp_cap = capital * float(opts["maxBuyingPowerPct"]) / 100.0
    by_bp = math.floor(bp_cap / cap_req["amount"]) if cap_req["amount"] > 0 else 0
    limits = {"riskBudget": by_risk, "buyingPower": by_bp}
    if mode != "market":
        f = float(metrics.get("kellyFraction") or 0.0) * float(opts["kellyFraction"])
        limits["kelly"] = math.floor(f * capital / cap_req["amount"]) if cap_req["amount"] > 0 else 0
    contracts = max(min(limits.values()), 0)
    binding = min(limits, key=limits.get)
    exceeded = False
    # Stock-backed income (covered call, CSP) is one lot or nothing: a 100-share
    # position rarely fits a 2% stress budget on a small account. Show one lot
    # when buying power allows and say plainly that it exceeds the risk budget,
    # rather than returning the useless "0 contracts" of v1 (A2).
    if contracts == 0 and ST.SPEC[structure]["risk"] == "stock" and by_bp >= 1:
        contracts, binding, exceeded = 1, "buyingPower", True
    return {
        "contractsSuggested": int(contracts),
        "binding": binding,
        "riskBudgetExceeded": exceeded,
        "stressLossPctOfAccount": round(stress / capital, 4),
        "limits": limits,
        "stressLossPerUnit": round(stress, 2),
        "stressSigmas": opts["stressSigmas"],
        "capitalRequired": round(cap_req["amount"], 2),
        "capitalBasis": cap_req["basis"],
        "capitalApproximate": cap_req["basis"].startswith("Reg-T"),
        "totalDebit": round(D.entry_cost(legs), 2),
        "pctOfAccount": round(cap_req["amount"] * max(contracts, 1) / capital, 4),
    }


def score(rank_metrics, net, cap_amount, liq, confidence):
    ev_cvar = rank_metrics.get("evPerCvar")
    c_ev = 0.5 if ev_cvar is None else min(max((ev_cvar + 0.5) / 1.0, 0.0), 1.0)
    c_pop = float(rank_metrics["pop"])
    theta_yield = (net["theta"] * 365.0 / cap_amount) if cap_amount > 0 else 0.0
    c_theta = min(max((theta_yield + 1.0) / 2.0, 0.0), 1.0)
    if liq["unknown"]:
        c_liq = 0.5
    else:
        spread = liq["bidAskSpread"]
        s_spread = 0.0 if spread is None else min(max(1.0 - spread / 0.05, 0.0), 1.0)
        s_oi = min((liq["openInterest"] or 0) / 500.0, 1.0)
        s_vol = min((liq["volume"] or 0) / 500.0, 1.0)
        c_liq = 0.5 * s_spread + 0.25 * s_oi + 0.25 * s_vol
    comps = {"evCvar": c_ev, "pop": c_pop, "theta": c_theta, "liquidity": c_liq, "confidence": confidence}
    total = sum(WEIGHTS[k] * comps[k] for k in WEIGHTS) * 10.0
    return round(total, 2), {k: round(v, 4) for k, v in comps.items()}


def confidence_of(payload, book, forecast, mode):
    smile = (payload.get("smiles") or {}).get(book.expiration) or {}
    rmse = smile.get("rmse")
    c_smile = 0.4 if rmse is None else min(max(1.0 - float(rmse) / 0.05, 0.0), 1.0)
    if mode == "market" or not forecast:
        c_fc = 1.0 if mode == "market" else 0.3
    else:
        lo, hi, s = forecast.get("sigmaLo"), forecast.get("sigmaHi"), forecast.get("sigma")
        c_fc = 0.5 if not (lo and hi and s) else min(max(1.0 - (hi - lo) / (2 * s), 0.0), 1.0)
    age = payload.get("dataAgeSeconds") or 0
    c_age = 1.0 if payload.get("pointInTime") else (1.0 if age < 900 else 0.6 if age < 86400 else 0.3)
    return round((c_smile + c_fc + c_age) / 3.0, 4)


def rationale(structure, m_rank, m_market, sizing, vrp_info, mode, net):
    bits = []
    if mode != "market":
        bits.append(f"EV ${m_rank['ev']:+.0f} on ${abs(m_rank['cvar05']):.0f} tail risk after ${m_rank['costs']:.0f} costs")
        if vrp_info:
            bits.append(f"IV {vrp_info['ivAtm']*100:.1f}% vs forecast {vrp_info['sigmaForecast']*100:.1f}%")
    else:
        bits.append(f"market EV ${m_market['ev']:+.0f} after ${m_market['costs']:.0f} costs (no edge assumed)")
    bits.append(f"{m_rank['pop']*100:.0f}% POP")
    bits.append(f"theta {net['theta']:+.2f}/day")
    bits.append(f"{sizing['contractsSuggested']} contract{'s' if sizing['contractsSuggested'] != 1 else ''}, limited by {sizing['binding']}")
    return " · ".join(bits)


# ---- the screen -----------------------------------------------------------------------------

def screen(payload, options=None, forecast=None, *, now=None):
    opts = {**DEFAULTS, **(options or {})}
    if "smiles" not in payload:
        volmodel.enrich(payload)
    asof = date.fromisoformat(payload["asof"])
    mode = opts["mode"]
    vrp_sign, view_sign, income, vrp_info = regime(payload, forecast, opts)
    if mode in ("vrp", "both") and not (forecast and forecast.get("sigma")):
        mode = "directional" if (mode == "both" and view_sign) else "market"
    warnings = []
    if mode != opts["mode"]:
        warnings.append(f"no volatility forecast available: ranking in {mode} mode")
    structs = eligible_structures(opts, vrp_sign if mode in ("vrp", "both") else 0,
                                  view_sign if mode in ("directional", "both") else 0, income)
    allow_indicative = bool(payload.get("stale")) or bool(payload.get("pointInTime"))

    books = []
    for exp in payload["expirations"]:
        chain = payload["chains"].get(exp)
        if not chain:
            continue
        b = ST.build_book(payload, exp, chain, allow_indicative=allow_indicative)
        if b and opts["minDTE"] <= b.dte <= opts["maxDTE"]:
            books.append(b)
    if not books:
        return envelope(payload, opts, mode, structs, [], warnings + ["no expirations inside the DTE window"], 0, vrp_info)

    cost_cfg = opts.get("costs") or {}
    sigma_f = float(forecast["sigma"]) if (forecast and forecast.get("sigma")) else None
    candidates = []
    generated = 0
    for i, book in enumerate(books):
        far = books[i + 1] if i + 1 < len(books) else None
        smile = (payload.get("smiles") or {}).get(book.expiration)
        try:
            market = (D.implied_density(Smile.from_dict(smile), book.spot, book.T, book.r, book.q)
                      if smile else D.lognormal_density(book.spot, book.T, book.atm_iv, drift=book.r - book.q))
        except Exception as exc:
            warnings.append(f"{book.expiration}: density failed ({exc}); lognormal used")
            market = D.lognormal_density(book.spot, book.T, book.atm_iv, drift=book.r - book.q)
        scen = None
        if mode != "market":
            _, s_impl = market.log_moments(book.forward)
            s_impl_ann = s_impl / math.sqrt(book.T)
            sf = sigma_f if mode in ("vrp", "both") else None
            drift = D.view_drift(opts["view"], opts["conviction"], sf or s_impl_ann, book.T) if mode in ("directional", "both") else 0.0
            scen = D.scenario_density(market, book.forward, book.T, sigma_forecast=sf, drift_shift=drift)
        conf = confidence_of(payload, book, forecast, mode)
        seen = set()
        for structure in structs:
            for params in ST.search_grid(structure):
                try:
                    legs = ST.build(structure, book, params, far)
                except Exception:
                    legs = None
                if not legs:
                    continue
                key = (structure, tuple((l["type"], l.get("strike"), l.get("expiration")) for l in legs))
                if key in seen:
                    continue
                seen.add(key)
                generated += 1
                for leg in legs:
                    leg.setdefault("r", book.r)
                    leg.setdefault("q", book.q)
                    if leg.get("T"):
                        leg["Trem"] = leg["T"] - book.T
                cand = evaluate(structure, legs, book, market, scen, payload, opts, mode, cost_cfg, conf, vrp_info, params)
                if cand:
                    candidates.append(cand)
    candidates.sort(key=lambda c: c["compositeScore"], reverse=True)
    top = candidates[: int(opts["topN"])]
    for c in top:
        try:
            c["payoff"]["profitAtExpiry"] = payoff_mod.payoff_curve(
                [{k: l[k] for k in ("type", "strike", "price", "qty") if k in l} for l in c["legs"]],
                payload["price"], num_points=61)
        except Exception:
            c["payoff"]["profitAtExpiry"] = []
    return envelope(payload, opts, mode, structs, top, warnings, len(candidates), vrp_info)


def evaluate(structure, legs, book, market, scen, payload, opts, mode, cost_cfg, conf, vrp_info, params):
    greeks = leg_greeks(legs, book)
    net = net_greeks(legs, greeks)
    plain = [{k: l[k] for k in ("type", "strike", "price", "qty") if k in l} for l in legs]
    cross_expiry = any(l.get("Trem") for l in legs)
    if cross_expiry:
        pnl = D.payoff_grid(legs, market.x)
        summary = {"max_profit": float(pnl.max()), "max_loss": float(-pnl.min()), "breakevens": []}
    else:
        summary = payoff_mod.payoff_summary(plain)
    cap_req = capital_requirement(structure, legs, summary, book)
    if cap_req["amount"] <= 0:
        return None
    # expected assignment-fee exposure: short legs' P(ITM) under the market density
    p_itm = []
    for l in legs:
        if l["type"].startswith("short") and not l["type"].endswith("stock"):
            p_itm.append(1 - market.cdf(l["strike"]) if l["type"].endswith("call") else market.cdf(l["strike"]))
        else:
            p_itm.append(None)
    cost = costmodel.position_costs(legs, cfg=cost_cfg, prob_itm_short=p_itm)
    m_market = D.position_metrics(legs, market, r=book.r, T=book.T, q=book.q, costs=cost["total"], capital=cap_req["amount"])
    m_rank = m_market
    m_scen = None
    if scen is not None:
        m_scen = D.position_metrics(legs, scen, r=book.r, T=book.T, q=book.q, costs=cost["total"], capital=cap_req["amount"])
        m_rank = m_scen
    stress = stress_loss(legs, book, opts["stressSigmas"])
    sizing = size(structure, legs, m_rank, cap_req, stress, opts, mode)
    liq = liquidity_of(legs)
    composite, comps = score(m_rank, net, cap_req["amount"], liq, conf)
    max_profit = summary["max_profit"] if math.isfinite(summary["max_profit"] or math.inf) else None
    max_loss = summary["max_loss"] if math.isfinite(summary["max_loss"] or math.inf) else None
    if max_profit is not None and max_profit > 1e9:
        max_profit = None
    if max_loss is not None and max_loss > 1e9:
        max_loss = None
    leg_out = []
    for l, g in zip(legs, greeks):
        o = {k: v for k, v in l.items() if k not in ("Trem",)}
        if g:
            o["greeks"] = g
        leg_out.append(o)
    return {
        "id": f"{structure}:{book.expiration}:" + "|".join(f"{l['type']}@{l.get('strike', 'S')}" for l in legs),
        "strategyType": structure,
        "structure": structure,
        "symbol": payload["symbol"],
        "expiration": book.expiration,
        "daysToExpiry": book.dte,
        "legs": leg_out,
        "netGreeks": net,
        "payoff": {"maxProfit": max_profit, "maxLoss": max_loss, "breakevens": summary.get("breakevens", []),
                   "profitAtExpiry": []},
        "probability": {"pop": m_rank["pop"], "probMaxProfit": m_rank["probMaxProfit"],
                        "popMarket": m_market["pop"]},
        "evaluation": {"market": m_market, "scenario": m_scen, "ranking": "scenario" if m_scen else "market",
                       "approx": ("cross-expiry: far leg marked at sticky-strike vol at the near expiry"
                                  if cross_expiry else None)},
        "costs": cost,
        "metrics": {
            "riskRewardRatio": (round(max_profit / max_loss, 2) if max_profit is not None and max_loss else None),
            "capitalEfficiency": (round(max_profit / cap_req["amount"], 3) if max_profit is not None else None),
            "thetaPerDay": net["theta"],
            "ev": m_rank["ev"], "evPct": m_rank["evPct"], "cvar05": m_rank["cvar05"],
            "evPerCvar": m_rank["evPerCvar"], "kellyFraction": m_rank["kellyFraction"],
        },
        "sizing": sizing,
        "liquidity": {**liq, "dataAgeSeconds": payload.get("dataAgeSeconds")},
        "meta": {
            "sigma": book.atm_iv, "riskFreeRate": book.r, "dividendYield": book.q, "spot": book.spot,
            "forward": round(book.forward, 4), "stale": bool(payload.get("stale")),
            "marksQuality": "indicative" if payload.get("stale") else "live",
            "provider": payload.get("provider"), "asof": payload["asof"],
            "volModel": f"smile:{(payload.get('smiles') or {}).get(book.expiration, {}).get('method', 'none')}",
            "exerciseStyle": payload.get("exerciseStyle"), "mode": mode, "params": params,
            "exposure": dict(zip(("delta", "vega", "theta"), ST.SPEC[structure]["exposure"])),
            "risk": ST.SPEC[structure]["risk"],
        },
        "compositeScore": composite,
        "scoreBreakdown": {"components": comps, "weights": dict(WEIGHTS), "version": 2},
        "rationale": rationale(structure, m_rank, m_market, sizing, vrp_info, mode, net),
    }


def envelope(payload, opts, mode, structs, candidates, warnings, generated, vrp_info):
    return {
        "symbol": payload["symbol"],
        "price": payload["price"],
        "ivRank": payload.get("ivRank"),
        "ivBand": None,
        "atmIv": payload.get("atmIvModel") or payload.get("atmIv"),
        "directionalView": opts["view"],
        "mode": mode,
        "vrp": vrp_info,
        "strategiesScreened": list(structs),
        "expirationsScreened": payload.get("expirations", []),
        "generated": generated,
        "candidates": candidates,
        "dataAgeSeconds": payload.get("dataAgeSeconds"),
        "stale": bool(payload.get("stale")),
        "provider": payload.get("provider"),
        "asof": payload.get("asof"),
        "warnings": warnings,
        "scoreVersion": 2,
    }
