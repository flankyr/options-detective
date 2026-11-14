"""Phase 4: calendar, costs, structures, optimizer screen."""
import json
import math
from datetime import date, datetime

import numpy as np
import pytest

import bsm
import costs
import mcal
import optimizer
import structures as ST
import volmodel


# ---- calendar ---------------------------------------------------------------------

def test_calendar_holidays_and_trading_days():
    assert not mcal.is_trading_day(date(2026, 11, 26))   # Thanksgiving
    assert not mcal.is_trading_day(date(2026, 11, 28))   # Saturday
    assert mcal.is_trading_day(date(2026, 11, 27))
    # Wed 25 Nov -> Fri 4 Dec: Fri 27 (half day), Mon 30, Tue 1, Wed 2, Thu 3, Fri 4 = 6
    assert mcal.trading_days_between(date(2026, 11, 25), date(2026, 12, 4)) == 6
    assert mcal.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)
    assert mcal.close_time("SPX").hour == 16 and mcal.close_time("SPX").minute == 15
    assert mcal.close_time("SPY").minute == 0
    T = mcal.year_fraction(date(2026, 11, 13), date(2026, 12, 18))
    assert T == pytest.approx(35 / 365)
    intraday = mcal.year_fraction(date(2026, 11, 13), date(2026, 12, 18), "SPY",
                                  now=datetime(2026, 11, 13, 10, 0))
    assert 34 / 365 < intraday < 35 / 365


# ---- costs -------------------------------------------------------------------------

def test_cost_model_components():
    legs = [{"type": "short_put", "strike": 95, "price": 1.0, "qty": 1, "bid": 0.95, "ask": 1.05},
            {"type": "long_put", "strike": 90, "price": 0.4, "qty": 1, "spreadPct": 0.10},
            {"type": "long_stock", "price": 100, "qty": 100}]
    c = costs.position_costs(legs)
    # half of 0.10 x 100 = 5 per side on leg 1; half of 0.04 x 100 = 2 per side on leg 2; x2 sides
    assert c["spread"] == pytest.approx((5 + 2) * 2)
    assert c["commission"] == pytest.approx(2 * 0.65 * 2)
    assert c["assignment"] == 0 and c["total"] == pytest.approx(c["spread"] + c["commission"])
    c2 = costs.position_costs(legs, prob_itm_short=[0.4, None, None])
    assert c2["assignment"] == pytest.approx(2.0)
    one_way = costs.position_costs(legs, cfg={"roundTrip": False})
    assert one_way["spread"] == pytest.approx(7)
    wide = costs.position_costs([{"type": "short_call", "strike": 110, "price": 2.0, "qty": 1, "indicativeOnly": True}])
    assert wide["spread"] == pytest.approx(0.5 * 0.08 * 2.0 * 1.5 * 100 * 2)


# ---- structures ----------------------------------------------------------------------

def chain_payload(spot=100.0, atm=0.22, skew=-0.15, dtes=(30, 60), r=0.04, q=0.01):
    asof = date(2026, 11, 13)
    payload = {"symbol": "TEST", "price": spot, "asof": asof.isoformat(), "dividendYield": q,
               "expirations": [], "chains": {}, "rates": {}, "dataAgeSeconds": 5, "stale": False}
    for dte in dtes:
        exp = date.fromordinal(asof.toordinal() + dte).isoformat()
        T = dte / 365
        F = spot * math.exp((r - q) * T)
        calls, puts = [], []
        for K in np.arange(70, 130.1, 2.5):
            k = math.log(K / F)
            iv = max(0.05, atm + skew * k + 0.3 * k * k)
            for ic, lst in ((True, calls), (False, puts)):
                mid = max(float(bsm.price(spot, K, T, r, q, iv, ic)), 0.01)
                half = max(0.05, mid * 0.01)
                lst.append({"strike": float(K), "bid": round(mid - half, 2), "ask": round(mid + half, 2),
                            "mid": round(mid, 4), "volume": 800, "openInterest": 4000,
                            "impliedVolatility": iv, "ivVendor": iv, "spreadPct": round(2 * half / mid, 4),
                            "timestamp": None, "illiquid": False, "indicativeOnly": False})
        payload["expirations"].append(exp)
        payload["chains"][exp] = {"calls": calls, "puts": puts}
        payload["rates"][exp] = r
    return volmodel.enrich(payload)


def test_book_and_delta_selection():
    p = chain_payload()
    exp = p["expirations"][0]
    book = ST.build_book(p, exp, p["chains"][exp])
    assert book is not None and book.dte == 30 and abs(book.forward - 100.25) < 0.1
    o = ST.by_delta([x for x in book.puts if x.strike < book.spot], 0.16)
    assert o is not None and abs(abs(o.delta) - 0.16) < 0.06 and o.strike < 100
    assert ST.strike_step(book) if False else book.strike_step() == 2.5
    c, pp = ST.paired_atm(book)
    assert c.strike == pp.strike == 100.0


def test_every_structure_builds_on_a_full_chain_with_expected_sign():
    p = chain_payload()
    e0, e1 = p["expirations"]
    near = ST.build_book(p, e0, p["chains"][e0])
    far = ST.build_book(p, e1, p["chains"][e1])
    for s in ST.ALL_STRUCTURES:
        built = False
        for params in ST.search_grid(s):
            legs = ST.build(s, near, params, far)
            if not legs:
                continue
            built = True
            assert len(legs) == ST.SPEC[s]["legs"], s
            cost = sum((1 if l["type"].startswith("long") else -1) * l["price"] * l["qty"] for l in legs if not l["type"].endswith("stock"))
            if s in ("call_credit_spread", "put_credit_spread", "iron_condor", "iron_butterfly",
                     "short_straddle", "short_strangle", "cash_secured_put", "covered_call"):
                assert cost < 0, s
            if s in ("call_vertical", "put_vertical", "long_straddle", "long_strangle", "long_call", "long_put", "calendar", "diagonal"):
                assert cost > 0, s
            if s in ("calendar", "diagonal"):
                assert legs[1]["expiration"] == e1 and legs[1]["T"] > near.T
        assert built, f"{s} never built"
    with pytest.raises(ValueError):
        ST.build("nope", near, {})
    assert ST.build("calendar", near, {}, None) is None


def test_eligibility_by_exposure_signature():
    assert ST.eligible("short_strangle", vrp_sign=1) and not ST.eligible("short_strangle", vrp_sign=-1)
    assert ST.eligible("long_straddle", vrp_sign=-1) and not ST.eligible("long_straddle", vrp_sign=1)
    assert ST.eligible("call_vertical", vrp_sign=1)          # vega-neutral: either regime
    assert ST.eligible("put_credit_spread", view_sign=1) and not ST.eligible("put_credit_spread", view_sign=-1)
    assert ST.eligible("iron_condor", view_sign=1)           # delta-neutral: any view
    assert ST.eligible("covered_call", income=True) and not ST.eligible("long_call", income=True)


# ---- optimizer -----------------------------------------------------------------------

def test_screen_market_mode_is_edge_free_and_json_serialisable():
    p = chain_payload()
    out = optimizer.screen(p, {"mode": "market", "minDTE": 1, "maxDTE": 400, "topN": 300}, None)
    assert out["scoreVersion"] == 2 and out["mode"] == "market" and out["candidates"]
    for c in out["candidates"]:
        if c["evaluation"]["approx"]:
            continue
        m = c["evaluation"]["market"]
        n = sum(l["qty"] for l in c["legs"] if not l["type"].endswith("stock"))
        assert abs(m["ev"] + m["costs"]) < 6 * n, c["id"]
        assert c["evaluation"]["scenario"] is None
        assert 0 <= c["compositeScore"] <= 10
    json.dumps(out)


def test_screen_vrp_rich_favours_short_vega_and_is_size_invariant():
    p = chain_payload(atm=0.22)
    fc = {"sigma": 0.15, "sigmaLo": 0.13, "sigmaHi": 0.17, "method": "har", "vrp": {"z": None}}
    a = optimizer.screen(p, {"mode": "vrp", "minDTE": 1, "maxDTE": 400}, fc)
    assert a["vrp"]["sign"] == 1
    assert all(c["meta"]["exposure"]["vega"] <= 0 for c in a["candidates"][:5])
    b = optimizer.screen(p, {"mode": "vrp", "minDTE": 1, "maxDTE": 400, "capital": 5_000_000}, fc)
    assert [c["id"] for c in a["candidates"]] == [c["id"] for c in b["candidates"]]
    cheap = optimizer.screen(p, {"mode": "vrp", "minDTE": 1, "maxDTE": 400},
                             {"sigma": 0.35, "method": "har", "vrp": {"z": None}})
    assert cheap["vrp"]["sign"] == -1
    assert all(c["meta"]["exposure"]["vega"] >= 0 for c in cheap["candidates"][:5])
    # z-score takes precedence over the raw ratio when the archive has history
    flat = optimizer.screen(p, {"mode": "vrp", "minDTE": 1, "maxDTE": 400},
                            {"sigma": 0.15, "method": "har", "vrp": {"z": 0.1}})
    assert flat["vrp"]["sign"] == 0


def test_screen_fallbacks_filters_and_sizing():
    p = chain_payload()
    out = optimizer.screen(p, {"mode": "both", "view": "neutral", "minDTE": 1, "maxDTE": 400}, None)
    assert out["mode"] == "market" and any("forecast" in w for w in out["warnings"])
    out2 = optimizer.screen(p, {"mode": "both", "view": "bullish", "minDTE": 1, "maxDTE": 400}, None)
    assert out2["mode"] == "directional"
    assert all(c["meta"]["exposure"]["delta"] >= 0 for c in out2["candidates"][:5])
    dr = optimizer.screen(p, {"mode": "market", "definedRiskOnly": True, "minDTE": 1, "maxDTE": 400, "topN": 300}, None)
    assert all(c["meta"]["risk"] != "undefined" for c in dr["candidates"])
    only = optimizer.screen(p, {"mode": "market", "allowedStrategies": ["put_vertical"], "minDTE": 1, "maxDTE": 400}, None)
    assert {c["strategyType"] for c in only["candidates"]} <= {"put_vertical", "call_credit_spread"}
    none = optimizer.screen(p, {"mode": "market", "minDTE": 100, "maxDTE": 120}, None)
    assert none["candidates"] == [] and none["warnings"]
    cc = [c for c in optimizer.screen(p, {"mode": "market", "allowedStrategies": ["covered_call"], "minDTE": 1, "maxDTE": 400}, None)["candidates"]]
    assert cc and all(c["sizing"]["contractsSuggested"] == 1 and c["sizing"]["riskBudgetExceeded"] for c in cc)
    assert all(c["payoff"]["profitAtExpiry"] for c in cc)


def test_screen_uses_lognormal_when_no_smile_and_handles_point_in_time():
    p = chain_payload()
    p.pop("smiles")
    p["volModel"] = None
    out = optimizer.screen(p, {"mode": "market", "minDTE": 1, "maxDTE": 400, "topN": 5}, None)
    assert out["candidates"] and all("smile:" in c["meta"]["volModel"] for c in out["candidates"])
    pit = dict(chain_payload(), pointInTime=True, provider="archive", stale=False)
    out2 = optimizer.screen(pit, {"mode": "market", "minDTE": 1, "maxDTE": 400, "topN": 5}, None)
    assert out2["candidates"][0]["meta"]["provider"] == "archive"
    assert out2["candidates"][0]["scoreBreakdown"]["components"]["confidence"] > 0.5
