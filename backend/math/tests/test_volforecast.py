import math

import numpy as np
import pytest

import volforecast as V


def garch_path(n=1500, seed=1, omega=1e-6, alpha=0.08, beta=0.90):
    rng = np.random.default_rng(seed)
    v = omega / (1 - alpha - beta)
    r = np.zeros(n)
    for i in range(n):
        v = omega + alpha * (r[i - 1] ** 2 if i else 0.0) + beta * v
        r[i] = rng.normal(0, math.sqrt(v))
    return r, 100 * np.exp(np.cumsum(r))


def test_log_returns_and_realized_vol():
    closes = [100, 101, 99, 100.5, float("nan"), 102]
    r = V.log_returns(closes)
    assert len(r) == 4  # NaN dropped
    assert V.realized_vol(r, 21) == pytest.approx(math.sqrt(np.mean(r * r) * 252))
    series = V.realized_vol_series(np.full(50, 0.01), 21)
    assert len(series) == 30 and series[0] == pytest.approx(0.01 * math.sqrt(252))


def test_garch_fit_recovers_simulated_parameters():
    r, _ = garch_path()
    fit = V.garch_fit(r)
    assert fit["alpha"] == pytest.approx(0.08, abs=0.04)
    assert fit["beta"] == pytest.approx(0.90, abs=0.05)
    assert fit["converged"]


def test_all_forecasters_land_near_the_true_vol_with_bands():
    r, closes = garch_path()
    true_sigma = math.sqrt(1e-6 / 0.02 * 252)  # unconditional
    for method in ("har", "ewma", "garch"):
        f = V.forecast(closes, 21, method)
        assert f["method"] == method
        assert 0.6 * true_sigma < f["sigma"] < 1.5 * true_sigma, (method, f["sigma"], true_sigma)
        assert f["nObs"] == len(closes) - 1
        assert f["realized21"] > 0 and f["realized63"] > 0
    har = V.forecast(closes, 21, "har")
    assert har["sigmaLo"] < har["sigma"] < har["sigmaHi"]
    assert har["nBacktest"] >= 20
    assert len(har["beta"]) == 4


def test_har_falls_back_to_ewma_on_short_history_and_rejects_tiny_samples():
    _, closes = garch_path(n=70)
    f = V.forecast(closes, 21, "har")
    assert f["method"] == "ewma" and f.get("fallbackFrom") == "har"
    with pytest.raises(ValueError):
        V.forecast(closes[:30], 21)
    with pytest.raises(ValueError):
        V.forecast(closes, 21, "magic")
    with pytest.raises(ValueError):
        V.garch_forecast(V.log_returns(closes[:80]), 21)


def test_har_reacts_to_a_vol_regime_change():
    rng = np.random.default_rng(7)
    calm = rng.normal(0, 0.10 / math.sqrt(252), 600)
    wild = rng.normal(0, 0.40 / math.sqrt(252), 60)
    closes = 100 * np.exp(np.cumsum(np.concatenate([calm, wild])))
    f = V.forecast(closes, 21, "har")
    assert f["sigma"] > 0.2  # the monthly component has caught the shift
    f_calm = V.forecast(100 * np.exp(np.cumsum(calm)), 21, "har")
    assert f_calm["sigma"] < 0.15


def test_vrp_and_zscore():
    assert V.vrp(None, 0.2) == {"vrp": None, "z": None, "n": 0}
    out = V.vrp(0.22, 0.18)
    assert out["vrp"] == pytest.approx(0.04) and out["ratio"] == pytest.approx(0.22 / 0.18, abs=1e-3) and out["z"] is None
    hist = [0.01, 0.02, 0.03, 0.04] * 6
    out = V.vrp(0.22, 0.18, hist)
    assert out["n"] == 24 and out["z"] > 0 and out["historyMean"] == pytest.approx(0.025)
    assert V.vrp(0.22, 0.18, [0.02] * 25)["z"] is None  # zero variance history: no z
