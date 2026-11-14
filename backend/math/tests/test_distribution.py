"""Phase 3: market-implied density, scenario densities, position metrics.

The first test is the anchor of the whole v2 redesign: under the market's own
distribution, a position priced on the same smile has EV = 0. Any 'edge' must
therefore come from a distribution that DIFFERS from the market's.
"""
import json
import math

import numpy as np
import pytest

import bsm
import distribution as D
import probability
from smile import fit_smile, svi_iv

S, T, R, Q = 500.0, 45 / 365, 0.0, 0.0
PARAMS = (0.0019, 0.1, -0.6, 0.0, 0.03)  # ~20% ATM, put skew, 45 days


def smile_for(T_=T):
    k = np.linspace(-0.3, 0.3, 15)
    return fit_smile(k, svi_iv(k, PARAMS, T_), T_)


def priced(legs, sm, r=R, q=Q, T_=T):
    F = S * math.exp((r - q) * T_)
    out = []
    for leg in legs:
        vol = float(sm.iv_strike(leg["strike"], F))
        price = bsm.bsm_price(S, leg["strike"], T_, r, vol, q, "call" if "call" in leg["type"] else "put")
        out.append({**leg, "price": price})
    return out


def test_ev_is_zero_under_the_market_density_for_every_structure():
    sm = smile_for()
    d = D.implied_density(sm, S, T, R, Q)
    structures = {
        "put_credit_spread": [{"type": "short_put", "strike": 480, "qty": 1}, {"type": "long_put", "strike": 470, "qty": 1}],
        "iron_condor": [{"type": "long_put", "strike": 450, "qty": 1}, {"type": "short_put", "strike": 460, "qty": 1},
                        {"type": "short_call", "strike": 540, "qty": 1}, {"type": "long_call", "strike": 550, "qty": 1}],
        "long_straddle": [{"type": "long_call", "strike": 500, "qty": 1}, {"type": "long_put", "strike": 500, "qty": 1}],
        "short_strangle": [{"type": "short_put", "strike": 470, "qty": 1}, {"type": "short_call", "strike": 530, "qty": 1}],
        "long_call": [{"type": "long_call", "strike": 510, "qty": 1}],
    }
    for name, legs in structures.items():
        m = D.position_metrics(priced(legs, sm), d, r=R, T=T)
        assert abs(m["ev"]) < 0.5, f"{name}: EV {m['ev']} should be ~0 under the market density"


def test_implied_density_is_proper_and_reprices_the_smile():
    sm = smile_for()
    d = D.implied_density(sm, S, T, R, Q)
    assert d.params["rawMass"] == pytest.approx(1.0, abs=1e-3)
    assert d.params["negativeMass"] == pytest.approx(0.0, abs=1e-6)
    assert d.mean == pytest.approx(S, rel=1e-3)  # risk-neutral mean = forward
    F = S
    for K in (470, 500, 530):
        vol = float(sm.iv_strike(K, F))
        assert d.expectation(np.maximum(d.x - K, 0.0)) == pytest.approx(bsm.bsm_price(S, K, T, R, vol, Q), abs=0.02)
    assert d.cdf(0) == 0 and d.cdf(1e9) == 1
    assert 0.0 < d.prob_between(480, 520) < 1.0
    # skew: put wing richer -> more mass below the forward than a lognormal would give
    ln = D.lognormal_density(S, T, float(sm.iv(0.0)))
    assert d.cdf(350) > ln.cdf(350) * 5  # fat left tail the ATM lognormal cannot see


def test_implied_density_with_rates_and_dividends_has_forward_mean():
    r, q = 0.05, 0.02
    sm = smile_for()
    d = D.implied_density(sm, S, T, r, q)
    assert d.mean == pytest.approx(S * math.exp((r - q) * T), rel=1e-3)
    legs = priced([{"type": "short_put", "strike": 480, "qty": 1}, {"type": "long_put", "strike": 470, "qty": 1}], sm, r, q)
    m = D.position_metrics(legs, d, r=r, T=T)
    assert abs(m["ev"]) < 0.5           # financing of the credit is accounted for
    assert m["financing"] < 0            # a credit earns carry


def test_lognormal_density_matches_v1_probability_module():
    sigma = 0.25
    d = D.lognormal_density(S, T, sigma, drift=0.0)
    for level in (450, 500, 550):
        assert d.cdf(level) == pytest.approx(probability.prob_below(S, level, T, sigma, 0.0), abs=2e-3)
    legs = [{"type": "short_put", "strike": 480, "price": 3.0, "qty": 1}, {"type": "long_put", "strike": 470, "price": 1.5, "qty": 1}]
    m = D.position_metrics(legs, d)
    assert m["pop"] == pytest.approx(probability.prob_of_profit(legs, S, T, sigma, 0.0), abs=3e-3)
    assert m["maxProfit"] == pytest.approx(150.0) and m["maxLoss"] == pytest.approx(850.0)


def test_scenario_density_rescales_dispersion_and_tilts_mean():
    sm = smile_for()
    market = D.implied_density(sm, S, T, R, Q)
    _, s_impl = market.log_moments(S)
    s_impl_ann = s_impl / math.sqrt(T)
    lower = D.scenario_density(market, S, T, sigma_forecast=s_impl_ann * 0.5)
    _, s_new = lower.log_moments(S)
    assert s_new / math.sqrt(T) == pytest.approx(s_impl_ann * 0.5, rel=1e-3)
    assert lower.std < market.std
    bull = D.scenario_density(market, S, T, drift_shift=D.view_drift("bullish", 1.0, s_impl_ann, T))
    assert bull.mean > market.mean * 1.02
    bear = D.scenario_density(market, S, T, drift_shift=D.view_drift("bearish", 0.5, s_impl_ann, T))
    assert bear.mean < market.mean
    assert D.view_drift("neutral", 1.0, 0.2, T) == 0.0 and D.view_drift("bullish", 0.0, 0.2, T) == 0.0
    # a short premium position gains EV when forecast vol is below implied
    legs = priced([{"type": "short_put", "strike": 470, "qty": 1}, {"type": "short_call", "strike": 530, "qty": 1}], sm)
    assert D.position_metrics(legs, lower)["ev"] > D.position_metrics(legs, market)["ev"] + 10


def test_position_metrics_cvar_kelly_costs_and_capital():
    sm = smile_for()
    d = D.implied_density(sm, S, T, R, Q)
    legs = priced([{"type": "short_put", "strike": 480, "qty": 1}, {"type": "long_put", "strike": 470, "qty": 1}], sm)
    m = D.position_metrics(legs, d, r=R, T=T, costs=12.0, capital=1000.0)
    assert m["ev"] == pytest.approx(-12.0, abs=0.6)       # costs come straight off a zero-EV trade
    assert m["evPct"] == pytest.approx(m["ev"] / 1000.0, abs=1e-6)
    assert -1000.0 <= m["cvar05"] <= m["var05"] <= 0.0     # worst-5% mean is at least as bad as the 5% quantile
    assert m["evPerCvar"] < 0 and 0.0 <= m["kellyFraction"] <= 1.0
    assert m["probMaxProfit"] <= m["pop"] <= 1.0
    assert m["entryCost"] < 0  # credit
    long_call = priced([{"type": "long_call", "strike": 510, "qty": 1}], sm)
    lc = D.position_metrics(long_call, d)
    assert lc["maxProfit"] is None or lc["maxProfit"] > 1e4  # unbounded upside shows as None/huge grid edge
    assert lc["maxLoss"] == pytest.approx(long_call[0]["price"] * 100, abs=0.01)


def test_payoff_grid_and_entry_cost_handle_stock_legs():
    x = np.array([90.0, 100.0, 110.0])
    legs = [{"type": "long_stock", "price": 100.0, "qty": 100}, {"type": "short_call", "strike": 105, "price": 2.0, "qty": 1}]
    assert np.allclose(D.payoff_grid(legs, x), [-1000 + 200, 200, 500 + 200])
    assert D.entry_cost(legs) == pytest.approx(10000 - 200)


def test_evaluate_position_envelope_and_json():
    sm = smile_for()
    legs = priced([{"type": "short_put", "strike": 480, "qty": 1}, {"type": "long_put", "strike": 470, "qty": 1}], sm)
    out = D.evaluate_position(legs, S, T, R, Q, smile=sm.to_dict(), mode="both", sigma_forecast=0.15,
                              view="bullish", conviction=0.5, costs=6.0, capital=1000, include_distribution=True)
    assert set(out) >= {"market", "scenario", "scenarioParams", "forward", "marketDistribution", "scenarioDistribution"}
    assert out["scenario"]["ev"] > out["market"]["ev"]  # lower vol + bullish tilt both help a put spread
    assert out["scenarioParams"]["sigmaForecast"] == 0.15 and out["scenarioParams"]["driftShift"] > 0
    assert 60 <= len(out["marketDistribution"]["x"]) <= 121
    json.dumps(out)
    only_market = D.evaluate_position(legs, S, T, R, Q, sigma=0.25, mode="market")
    assert "scenario" not in only_market and only_market["market"]["distribution"] == "lognormal"
    with pytest.raises(ValueError):
        D.evaluate_position(legs, S, T, R, Q)
    rt = D.Distribution.from_dict(out["marketDistribution"])
    full = D.implied_density(sm, S, T, R, Q)
    assert rt.cdf(500) == pytest.approx(full.cdf(500), abs=0.03)
    assert rt.cdf(450) == pytest.approx(full.cdf(450), abs=0.03)
