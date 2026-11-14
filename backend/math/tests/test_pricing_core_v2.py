"""Phase 2 pricing core: BSM with dividends, American exercise, IV, smiles."""
import json
import math
import time
from datetime import date

import numpy as np
import pytest

import american
import black_scholes as bs
import bsm
import engine
import smile
import volmodel
from smile import svi_iv


# ---- BSM ---------------------------------------------------------------------

@pytest.mark.parametrize("S,K,T,r,sigma", [(100, 100, 1, 0.05, 0.2), (100, 80, 0.5, 0.01, 0.4),
                                            (50, 70, 2, 0.03, 0.15), (100, 100, 0.01, 0.05, 0.3)])
def test_bsm_reduces_to_v1_black_scholes_when_q_is_zero(S, K, T, r, sigma):
    assert bsm.bsm_price(S, K, T, r, sigma, 0.0, "call") == pytest.approx(bs.bs_call_price(S, K, T, r, sigma), rel=1e-12)
    assert bsm.bsm_price(S, K, T, r, sigma, 0.0, "put") == pytest.approx(bs.bs_put_price(S, K, T, r, sigma), rel=1e-12)
    g2 = bsm.bsm_greeks(S, K, T, r, sigma, 0.0, "put")
    g1 = bs.bs_put_greeks(S, K, T, r, sigma)
    for k in g1:
        assert g2[k] == pytest.approx(g1[k], rel=1e-9, abs=1e-12)


def test_bsm_put_call_parity_with_dividends():
    S, K, T, r, q, sig = 100, 95, 0.75, 0.04, 0.03, 0.25
    c = bsm.bsm_price(S, K, T, r, sig, q, "call")
    p = bsm.bsm_price(S, K, T, r, sig, q, "put")
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-10)


def test_bsm_dividends_lower_calls_raise_puts_and_greeks_are_finite_differences():
    c0 = bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.0)
    c3 = bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.03)
    assert c3 < c0
    assert bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.03, "put") > bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.0, "put")
    g = bsm.bsm_greeks(100, 100, 1, 0.05, 0.2, 0.03)
    h = 1e-4
    fd_delta = (bsm.bsm_price(100 + h, 100, 1, 0.05, 0.2, 0.03) - bsm.bsm_price(100 - h, 100, 1, 0.05, 0.2, 0.03)) / (2 * h)
    assert g["delta"] == pytest.approx(fd_delta, rel=1e-6)
    fd_vega = (bsm.bsm_price(100, 100, 1, 0.05, 0.2 + 0.0001, 0.03) - bsm.bsm_price(100, 100, 1, 0.05, 0.2 - 0.0001, 0.03)) / 0.0002 / 100
    assert g["vega"] == pytest.approx(fd_vega, rel=1e-5)
    fd_theta = -(bsm.bsm_price(100, 100, 1 + 1 / 365, 0.05, 0.2, 0.03) - bsm.bsm_price(100, 100, 1 - 1 / 365, 0.05, 0.2, 0.03)) / 2
    assert g["theta"] == pytest.approx(fd_theta, rel=1e-4)


def test_bsm_degenerate_inputs_and_validation():
    assert bsm.bsm_price(110, 100, 0, 0.05, 0.2) == 10.0
    assert bsm.bsm_price(90, 100, 0.5, 0.05, 0.0, 0.0, "put") == pytest.approx(100 * math.exp(-0.025) - 90)
    g = bsm.bsm_greeks(110, 100, 0, 0.05, 0.2)
    assert g == {"delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    with pytest.raises(ValueError):
        bsm.bsm_price(-1, 100, 1, 0.05, 0.2)
    with pytest.raises(ValueError):
        bsm.bsm_price(100, 100, 1, 0.05, -0.2)


def test_bsm_vectorised_matches_scalar_and_is_fast():
    K = np.linspace(50, 150, 2000)
    t0 = time.perf_counter()
    p = bsm.price(100, K, 0.5, 0.04, 0.01, 0.25, True)
    iv = bsm.implied_vol(p, 100, K, 0.5, 0.04, 0.01, True)
    elapsed = time.perf_counter() - t0
    assert p.shape == (2000,)
    assert p[0] == pytest.approx(bsm.bsm_price(100, 50, 0.5, 0.04, 0.25, 0.01))
    assert np.nanmax(np.abs(iv - 0.25)) < 1e-6
    assert elapsed < 1.5  # budget: whole chain in well under the 300 ms target on a dev box; CI slack


def test_bsm_implied_vol_edge_cases():
    # at intrinsic -> 0; above the upper bound -> NaN; expired -> NaN; vectorised mix
    assert bsm.bsm_implied_vol(100 - 95 * math.exp(-0.05), 100, 95, 1, 0.05, 0.0, "call") == 0.0
    with pytest.raises(ValueError):
        bsm.bsm_implied_vol(101, 100, 95, 1, 0.05, 0.0, "call")
    with pytest.raises(ValueError):
        bsm.bsm_implied_vol(5, 100, 95, 0, 0.05)
    out = bsm.implied_vol([10.4506, 500.0], 100, 100, 1, 0.05, 0.0, [True, True])
    assert out[0] == pytest.approx(0.2, abs=1e-4) and math.isnan(out[1])
    # deep OTM tiny vega: still converges via bisection polish
    p = bsm.bsm_price(100, 160, 0.1, 0.04, 0.45)
    assert bsm.bsm_implied_vol(p, 100, 160, 0.1, 0.04) == pytest.approx(0.45, abs=1e-5)


# ---- American ------------------------------------------------------------------

def test_crr_converges_to_bsm_for_european():
    e = bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.02)
    c = american.crr_price(100, 100, 1, 0.05, 0.02, 0.2, True, steps=800, american=False)[0]
    assert c == pytest.approx(e, abs=0.01)


def test_american_put_above_european_and_call_equal_without_dividends():
    euro_put = bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.0, "put")
    am_put = american.crr_price(100, 100, 1, 0.05, 0.0, 0.2, False)[0]
    assert am_put > euro_put + 0.3
    assert american.baw_price(100, 100, 1, 0.05, 0.0, 0.2, False) == pytest.approx(am_put, abs=0.03)
    # textbook value for this put is ~6.09
    assert am_put == pytest.approx(6.09, abs=0.02)
    assert american.baw_price(100, 100, 1, 0.05, 0.0, 0.2, True) == pytest.approx(bsm.bsm_price(100, 100, 1, 0.05, 0.2))
    assert american.critical_price(100, 100, 1, 0.05, 0.0, 0.2, True) is None


def test_american_call_on_dividend_payer_has_early_exercise_premium():
    euro = bsm.bsm_price(100, 100, 1, 0.05, 0.2, 0.08)
    crr = american.crr_price(100, 100, 1, 0.05, 0.08, 0.2, True)[0]
    baw = american.baw_price(100, 100, 1, 0.05, 0.08, 0.2, True)
    assert crr > euro + 0.2 and baw == pytest.approx(crr, abs=0.05)
    g = american.american_greeks(100, 100, 1, 0.05, 0.2, 0.08, "call")
    assert g["earlyExercisePremium"] > 0 and g["criticalPrice"] > 100
    assert set(g) >= {"price", "delta", "gamma", "theta", "vega", "rho", "europeanPrice"}


def test_american_deep_itm_put_is_intrinsic_and_boundary_below_spot():
    assert american.baw_price(60, 100, 1, 0.05, 0.0, 0.2, False) == 40.0
    assert american.crr_price(60, 100, 1, 0.05, 0.0, 0.2, False)[0] == pytest.approx(40.0, abs=1e-9)
    sstar = american.critical_price(100, 100, 1, 0.05, 0.0, 0.2, False)
    assert 60 < sstar < 100


def test_baw_vectorised_is_fast_and_close_to_crr_across_strikes():
    K = np.linspace(60, 140, 2000)
    t0 = time.perf_counter()
    p = american.baw_price(100, K, 0.3, 0.04, 0.01, 0.25, np.tile([True, False], 1000))
    assert time.perf_counter() - t0 < 2.0
    assert np.all(np.isfinite(p)) and p.shape == (2000,)
    for k in (80, 100, 120):
        crr = american.crr_price(100, k, 0.5, 0.04, 0.0, 0.3, False, steps=400)[0]
        assert american.baw_price(100, k, 0.5, 0.04, 0.0, 0.3, False) == pytest.approx(crr, abs=0.1)


def test_exercise_style_table():
    assert american.exercise_style("SPX") == "european"
    assert american.exercise_style("xsp") == "european"
    assert american.exercise_style("SPY") == "american"
    assert american.exercise_style("AAPL") == "american"


# ---- smile ---------------------------------------------------------------------

def test_svi_fit_recovers_synthetic_smile_and_passes_durrleman():
    T = 0.25
    k = np.linspace(-0.3, 0.3, 15)
    true = (0.04, 0.4, -0.4, 0.0, 0.1)
    iv = svi_iv(k, true, T)
    sm = smile.fit_smile(k, iv, T)
    assert sm.method == "svi" and sm.rmse < 1e-3 and sm.arbitrage_free
    assert np.allclose(sm.iv([-0.1, 0.0, 0.1]), svi_iv([-0.1, 0.0, 0.1], true, T), atol=2e-3)
    # skew direction preserved: puts (k<0) richer than calls
    assert sm.iv(-0.2) > sm.iv(0.2)
    rt = smile.Smile.from_dict(json.loads(json.dumps(sm.to_dict())))
    assert np.allclose(rt.iv(k), sm.iv(k))


def test_smile_falls_back_to_spline_with_few_points_and_flat_wings():
    T = 0.1
    k = np.array([-0.1, 0.0, 0.1])
    iv = np.array([0.3, 0.25, 0.24])
    sm = smile.fit_smile(k, iv, T)
    assert sm.method == "spline" and sm.n_used == 3
    assert sm.iv(-0.1) == pytest.approx(0.3) and sm.iv(-0.8) == pytest.approx(0.3)  # flat beyond range
    assert sm.iv(0.5) == pytest.approx(0.24)
    one = smile.fit_smile([0.0], [0.2], T)
    assert one.method == "flat" and one.iv(0.7) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        smile.fit_smile([0.0, 0.1], [float("nan"), 7.0], T)


def test_durrleman_detects_butterfly_arbitrage():
    good = (0.04, 0.4, -0.4, 0.0, 0.1)
    assert np.all(smile.durrleman_g(np.linspace(-1, 1, 50), good) > 0)
    bad = (0.0, 0.9, -0.99, 0.0, 0.001)  # kinked, near-degenerate wings
    assert np.any(smile.durrleman_g(np.linspace(-1, 1, 200), bad) < 0)


def test_total_variance_interpolation_between_expiries():
    s1 = smile.fit_smile(np.linspace(-0.2, 0.2, 7), np.full(7, 0.20), 0.1)
    s2 = smile.fit_smile(np.linspace(-0.2, 0.2, 7), np.full(7, 0.30), 0.5)
    mid = smile.total_variance_interp({0.1: s1, 0.5: s2}, 0.3, 0.0)
    w = (0.04 * 0.1 + 0.09 * 0.5) / 2
    assert float(mid) == pytest.approx(math.sqrt(w / 0.3), abs=2e-3)
    assert float(smile.total_variance_interp({0.1: s1, 0.5: s2}, 1.0, 0.0)) == pytest.approx(0.30, abs=2e-3)


# ---- volmodel enrichment ---------------------------------------------------------

def synthetic_payload(vendor_tilt=1.0):
    S, asof = 500.0, "2026-11-13"
    exps = {"2026-12-18": 35, "2027-01-15": 63}
    payload = {"symbol": "SPY", "price": S, "asof": asof, "rates": {e: 0.038 for e in exps},
               "dividendYield": 0.012, "expirations": list(exps), "chains": {}}
    for e, d in exps.items():
        T = d / 365
        F = S * math.exp((0.038 - 0.012) * T)
        K = np.arange(400, 601, 5.0)
        iv = svi_iv(np.log(K / F), (0.004 * T * 4, 0.3, -0.5, 0.0, 0.15), T)
        calls, puts = [], []
        for Ki, ivi in zip(K, iv):
            for ic, lst in ((True, calls), (False, puts)):
                p = float(bsm.price(S, Ki, T, 0.038, 0.012, ivi, ic))
                b, a = max(p - 0.05, 0.0), p + 0.05
                lst.append({"strike": float(Ki), "bid": b, "ask": a, "mid": round((a + b) / 2, 4),
                            "spreadPct": 0.1 / max(p, 0.01), "impliedVolatility": float(ivi) * vendor_tilt,
                            "ivVendor": float(ivi) * vendor_tilt, "volume": 100, "openInterest": 100})
        payload["chains"][e] = {"calls": calls, "puts": puts}
    return payload


def test_enrich_solves_iv_fits_smiles_and_flags_vendor_deviation():
    out = volmodel.enrich(synthetic_payload())
    assert out["volModel"]["expirations"] == 2 and out["volModel"]["noIv"] == 0
    sm = out["smiles"]["2026-12-18"]
    assert sm["method"] == "svi" and sm["rmse"] < 1e-3 and sm["arbitrageFree"]
    put = out["chains"]["2026-12-18"]["puts"][5]
    assert put["ivModel"] == pytest.approx(put["ivVendor"], rel=1e-3)
    assert put["ivSmile"] == pytest.approx(put["ivModel"], rel=1e-2)
    assert put["ivFlag"] is None and out["atmIvModel"] > 0
    tilted = volmodel.enrich(synthetic_payload(vendor_tilt=1.3))
    assert tilted["volModel"]["vendorDeviations"] > 100  # every contract's vendor IV is 30% off
    json.dumps(tilted)


def test_enrich_handles_bookless_and_expired_rows():
    p = synthetic_payload()
    p["chains"]["2026-12-18"]["calls"][0]["mid"] = None
    p["chains"]["2026-12-18"]["calls"][1]["mid"] = 1000.0  # above the no-arb bound
    out = volmodel.enrich(p)
    c0, c1 = out["chains"]["2026-12-18"]["calls"][:2]
    assert c0["ivModel"] is None and c0["ivFlag"] == "noIv" and c0["ivSmile"] is not None
    assert c1["ivModel"] is None and c1["ivFlag"] == "noIv"
    assert out["volModel"]["noIv"] == 2
    expired = {"symbol": "X", "price": 100, "asof": "2026-11-13", "expirations": ["2026-11-13"],
               "chains": {"2026-11-13": {"calls": [{"strike": 100, "mid": 1}], "puts": []}}}
    out2 = volmodel.enrich(expired)
    assert out2["smiles"] == {} and out2["atmIvModel"] is None


def test_engine_dispatch_exposes_v2_functions():
    for fn in ("bsm_price", "bsm_greeks", "bsm_implied_vol", "american_price", "american_greeks",
               "exercise_style", "smile_fit", "smile_iv"):
        assert fn in engine.DISPATCH
    resp = engine.handle(json.dumps({"fn": "american_greeks", "args": {
        "S": 100, "K": 105, "T": 0.5, "r": 0.04, "sigma": 0.3, "q": 0.02, "option_type": "put"}}))
    assert resp["ok"] and resp["result"]["earlyExercisePremium"] > 0
    sm = engine.handle(json.dumps({"fn": "smile_fit", "args": {
        "k": list(np.linspace(-0.3, 0.3, 9)), "iv": [0.3, 0.27, 0.25, 0.23, 0.22, 0.22, 0.225, 0.23, 0.24], "T": 0.2}}))
    assert sm["ok"] and sm["result"]["method"] in ("svi", "spline")
    ev = engine.handle(json.dumps({"fn": "smile_iv", "args": {"smile": {**sm["result"], "forward": 100.0},
                                                               "strikes": [90, 100, 110]}}))
    assert ev["ok"] and len(ev["result"]) == 3 and ev["result"][0] > ev["result"][2]
