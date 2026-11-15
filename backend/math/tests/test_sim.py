"""Phase 5: backtester accounting, no look-ahead, calibration, metrics, walk-forward."""
import math
import os

import numpy as np
import pytest

import sim_fixtures as F
from sim import calibration, metrics
from sim.backtest import Backtester
from sim.runner import handle, run_backtest
from sim.strategies import registry
from sim.strategy import Strategy
from sim.walkforward import forecaster_walkforward, walk_forward


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("sim"))
    os.makedirs(os.path.join(root, "archive"))
    dates = F.build_archive(os.path.join(root, "archive"), days=90)
    return root, dates, F.service_for(root)


def test_registry_has_builtins_and_describe():
    reg = registry()
    assert {"optimizer", "strangle_16d", "put_spread_vrp", "buy_and_hold"} <= set(reg)
    d = reg["strangle_16d"](delta=0.25).describe()
    assert d["name"] == "strangle_16d" and d["params"]["delta"] == 0.25 and d["doc"]


def test_backtest_accounting_closes_and_calibration(world):
    root, dates, svc = world
    r = run_backtest(svc, "strangle_16d", {}, {"symbol": "TEST", "start": dates[0], "end": dates[-1],
                                              "initialCapital": 100_000, "useForecast": False})
    m = r["metrics"]
    assert m["days"] == len(dates) and m["trades"] >= 2
    # cash accounting: final equity = initial + sum of realized trade P&L (all positions liquidated)
    assert m["finalEquity"] == pytest.approx(100_000 + sum(t["pnl"] for t in r["trades"]), abs=0.05)
    assert all(t["costs"] > 0 for t in r["trades"])
    assert all(t["mae"] - t["costs"] - 1e-6 <= t["pnl"] <= t["mfe"] + 1e-6 for t in r["trades"])
    assert all(t["opened"] < t["closed"] <= t["expiration"] for t in r["trades"] if t["reason"] != "end")
    assert r["calibration"]["n"] == len(r["trades"])
    assert r["calibration"]["pop"]["brier"] is not None and r["calibration"]["pop"]["buckets"]
    assert r["curve"][0]["date"] == dates[0] and r["curve"][-1]["date"] == dates[-1]
    assert "sharpe" in m and "maxDrawdown" in m and m["maxDrawdown"] <= 0


def test_backtest_optimizer_strategy_uses_forecast_once_history_exists(world):
    root, dates, svc = world
    r = run_backtest(svc, "optimizer", {"mode": "vrp", "topK": 1, "maxOpen": 2, "allowedStrategies": ["iron_condor", "put_credit_spread"]},
                     {"symbol": "TEST", "start": dates[0], "end": dates[-1], "initialCapital": 100_000, "screenEvery": 5})
    assert r["trades"]
    modes = {t["meta"]["mode"] for t in r["trades"]}
    assert "vrp" in modes  # after ~61 archived closes the forecast kicks in
    assert all(t["structure"] in ("iron_condor", "put_credit_spread") for t in r["trades"])
    assert all(t["predictedPopMarket"] is not None for t in r["trades"])
    assert r["elapsedSeconds"] < 120


def test_no_look_ahead_positions_only_see_their_entry_chain(world):
    root, dates, svc = world
    seen = []

    class Spy(Strategy):
        name = "spy_strategy"
        params = {}

        def entries(self, ctx):
            seen.append((ctx.asof, max(ctx.payload["expirations"]), ctx.payload["asof"]))
            return []

    bt = Backtester(svc, Spy(), {"symbol": "TEST", "start": dates[0], "end": dates[20], "screenEvery": 1, "useForecast": False})
    bt.run()
    assert seen and all(asof == payload_asof for asof, _, payload_asof in seen)
    assert [a for a, _, _ in seen] == dates[:20]  # the last day never screens (nothing to hold)


def test_buy_and_hold_tracks_the_underlying(world):
    root, dates, svc = world
    r = run_backtest(svc, "buy_and_hold", {}, {"symbol": "TEST", "start": dates[0], "end": dates[-1],
                                              "initialCapital": 50_000, "useForecast": False})
    first, last = r["curve"][0]["spot"], r["curve"][-1]["spot"]
    assert r["metrics"]["totalReturn"] == pytest.approx((last - first) * 100 / 50_000, abs=1e-4)
    assert r["calibration"]["pop"] is None


def test_runner_handle_commands(world):
    root, dates, svc = world
    assert handle({"cmd": "strategies"}, svc)["ok"]
    bad = handle({"cmd": "run", "strategy": "nope", "config": {"symbol": "TEST"}}, svc)
    assert bad["ok"] is False and "unknown strategy" in bad["error"]
    res = handle({"cmd": "run", "strategy": "buy_and_hold", "config": {"symbol": "TEST", "start": dates[0], "end": dates[10], "useForecast": False}}, svc)
    assert res["ok"] and os.path.exists(res["result"]["savedTo"])
    runs = handle({"cmd": "runs"}, svc)
    assert runs["ok"] and runs["result"][-1]["strategy"] == "buy_and_hold"
    assert handle({}, svc)["ok"] is False
    empty = handle({"cmd": "run", "strategy": "buy_and_hold", "config": {"symbol": "TEST", "start": "2031-01-01", "end": "2031-02-01"}}, svc)
    assert empty["ok"] is False


def test_metrics_and_calibration_units():
    curve = [{"equity": 100 * (1 + 0.001) ** i} for i in range(300)]
    trades = [{"pnl": 10, "costs": 1, "daysHeld": 5, "entryCost": -2, "contracts": 1}] * 15 + \
             [{"pnl": -5, "costs": 1, "daysHeld": 5, "entryCost": -2, "contracts": 1}] * 5
    m = metrics.summarize(curve, trades, 100)
    assert m["winRate"] == 0.75 and m["profitFactor"] == pytest.approx(150 / 25)
    assert m["maxDrawdown"] == 0.0 and m["sharpe"] is None  # constant growth: zero variance
    assert m["cagr"] == pytest.approx((1.001 ** 299) ** (252 / 299) - 1, rel=1e-6)
    rng = np.random.default_rng(0)
    noisy = [{"equity": 100 * math.exp(x)} for x in np.cumsum(rng.normal(0.0005, 0.01, 400))]
    m2 = metrics.summarize(noisy, [], 100)
    assert m2["sharpeCI"] is not None and m2["sharpeCI"][0] < m2["sharpe"] < m2["sharpeCI"][1]
    cal = calibration.calibrate([
        {"pnl": 1, "pnlPerUnit": 1, "predictedPop": 0.75, "predictedEv": 2, "predictedPopMarket": 0.7},
        {"pnl": -1, "pnlPerUnit": -1, "predictedPop": 0.75, "predictedEv": 2, "predictedPopMarket": 0.7},
        {"pnl": 1, "pnlPerUnit": 1, "predictedPop": 0.75, "predictedEv": 2, "predictedPopMarket": 0.7},
        {"pnl": 1, "pnlPerUnit": 1, "predictedPop": 0.75, "predictedEv": 2, "predictedPopMarket": 0.7},
    ])
    assert cal["pop"]["buckets"][0]["realized"] == 0.75 and cal["pop"]["brier"] == pytest.approx(0.1875)
    assert cal["ev"] is None  # fewer than 5 trades with EV
    assert calibration.calibrate([])["pop"] is None


def test_walk_forward_generic_and_forecaster():
    dates = [f"d{i:03d}" for i in range(400)]
    calls = []

    def run_fn(params, start, end):
        calls.append((params["x"], start, end))
        good = params["x"] == 2
        return {"metrics": {"sharpe": 2.0 if good else 0.5}, "curve": [{"date": start, "equity": 1}]}

    out = walk_forward(run_fn, [{"x": 1}, {"x": 2}], dates, train_days=200, test_days=50)
    assert len(out["windows"]) == 4 and all(w["params"] == {"x": 2} for w in out["windows"])
    assert out["windows"][0]["train"] == ("d000", "d199") and out["windows"][0]["test"] == ("d200", "d249")
    assert len(out["stitchedCurve"]) == 4

    rng = np.random.default_rng(2)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.2 / math.sqrt(252), 700)))
    fw = forecaster_walkforward(closes, horizon=21, step=20, methods=("har", "ewma"), min_obs=300)
    assert fw["best"] in ("har", "ewma") and fw["methods"]["ewma"]["n"] > 10
    assert 0 < fw["methods"]["har"]["rmseLog"] < 1
