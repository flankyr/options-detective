// Detector v2 (Phase 4): the screen runs through math/optimizer.py.
const test = require("node:test");
const assert = require("node:assert/strict");

const { engineClient } = require("../services/mathEngine");
const { createDetector, DEFAULTS, WEIGHTS } = require("../services/detector");
const { fakeDataLayer, realisticMarketData } = require("./fixtures");
const { engineTest } = require("./engineAvailable");

test.after(() => engineClient.shutdown());

const forecastOf = (sigma) => async () => ({ method: "har", sigma, sigmaLo: sigma * 0.85, sigmaHi: sigma * 1.15, horizon: 30, vrp: { z: null } });
const noForecast = async () => null;

async function screenWith({ data, forecaster = noForecast, ...params }) {
  const det = createDetector({ dataLayer: fakeDataLayer(data), forecaster });
  return det.screen({ symbol: "TEST", minDTE: 1, maxDTE: 400, ...params });
}

test("v2 screen: ranked candidates with a size-invariant score v2 breakdown", engineTest, async () => {
  const data = await realisticMarketData();
  const res = await screenWith({ data, mode: "vrp", forecaster: forecastOf(0.16) });
  assert.equal(res.scoreVersion, 2);
  assert.equal(res.mode, "vrp");
  assert.ok(res.candidates.length >= 10, `got ${res.candidates.length}`);
  assert.ok(res.generated >= res.candidates.length);
  for (let i = 1; i < res.candidates.length; i += 1) {
    assert.ok(res.candidates[i - 1].compositeScore >= res.candidates[i].compositeScore);
  }
  for (const c of res.candidates) {
    assert.deepEqual(Object.keys(c.scoreBreakdown.weights).sort(), Object.keys(WEIGHTS).sort());
    for (const [k, v] of Object.entries(c.scoreBreakdown.components)) {
      assert.ok(v >= 0 && v <= 1, `${k}=${v}`);
    }
    const recomputed = Object.entries(WEIGHTS).reduce((acc, [k, w]) => acc + w * c.scoreBreakdown.components[k], 0) * 10;
    assert.ok(Math.abs(recomputed - c.compositeScore) < 0.02);
    assert.ok(c.evaluation.market && c.evaluation.scenario);
    assert.ok(c.payoff.profitAtExpiry.length === 61);
    assert.ok(c.legs.every((l) => l.type.endsWith("stock") || (l.ivSource === "smile" && l.greeks)));
  }
  // ranking does not depend on account size (score is per-unit, size-invariant)
  const big = await screenWith({ data, mode: "vrp", forecaster: forecastOf(0.16), capital: 2_500_000 });
  assert.deepEqual(big.candidates.map((c) => c.id), res.candidates.map((c) => c.id));
  assert.deepEqual(big.candidates.map((c) => c.compositeScore), res.candidates.map((c) => c.compositeScore));
});

test("v2 screen: market mode has no edge — every single-expiry candidate's EV is about minus its costs", engineTest, async () => {
  const data = await realisticMarketData();
  const res = await screenWith({ data, mode: "market", topN: 200 });
  assert.equal(res.mode, "market");
  const single = res.candidates.filter((c) => !c.evaluation.approx);
  assert.ok(single.length > 20);
  for (const c of single) {
    const m = c.evaluation.market;
    // the density is the smile FIT, legs are priced off raw mids: a few dollars of
    // fit error on deep wings is expected and honest; a real edge would be far larger
    const contracts = c.legs.filter((l) => !l.type.endsWith("stock")).reduce((a, l) => a + l.qty, 0);
    assert.ok(Math.abs(m.ev + m.costs) < 6 * contracts, `${c.id}: ev ${m.ev} costs ${m.costs}`);
    assert.ok(m.costs > 0);
  }
  assert.ok(res.candidates.every((c) => c.evaluation.scenario === null));
});

test("v2 screen: VRP sign picks the vega side; a cheap-vol forecast flips it", engineTest, async () => {
  const data = await realisticMarketData({ atmIv: 0.22 });
  const rich = await screenWith({ data, mode: "vrp", forecaster: forecastOf(0.15) });
  assert.equal(rich.vrp.sign, 1);
  assert.ok(rich.strategiesScreened.includes("short_strangle") && rich.strategiesScreened.includes("iron_condor"));
  assert.ok(!rich.strategiesScreened.includes("long_straddle"));
  const top = rich.candidates.slice(0, 5);
  assert.ok(top.every((c) => c.meta.exposure.vega <= 0), top.map((c) => c.strategyType).join(","));
  assert.ok(top.every((c) => c.evaluation.scenario.ev > c.evaluation.market.ev));

  const cheap = await screenWith({ data, mode: "vrp", forecaster: forecastOf(0.32) });
  assert.equal(cheap.vrp.sign, -1);
  assert.ok(cheap.strategiesScreened.includes("long_straddle"));
  assert.ok(!cheap.strategiesScreened.includes("short_strangle"));
  assert.ok(cheap.candidates.slice(0, 5).every((c) => c.meta.exposure.vega >= 0));
});

test("v2 screen: directional view tilts toward aligned delta; income requires positive theta", engineTest, async () => {
  const data = await realisticMarketData();
  const bull = await screenWith({ data, mode: "directional", directionalView: "bullish", conviction: 0.8 });
  assert.ok(bull.candidates.length > 0);
  assert.ok(bull.strategiesScreened.every((s) => !["long_put", "put_vertical", "call_credit_spread"].includes(s)));
  assert.ok(bull.candidates.slice(0, 5).every((c) => c.meta.exposure.delta >= 0));
  const income = await screenWith({ data, mode: "market", directionalView: "income" });
  assert.ok(income.strategiesScreened.every((s) => !["long_call", "long_put", "long_straddle", "long_strangle"].includes(s)));
});

test("v2 sizing: income trades size above zero; stress loss, not stock-to-zero, is the risk unit (A2)", engineTest, async () => {
  const data = await realisticMarketData();
  const res = await screenWith({ data, mode: "vrp", forecaster: forecastOf(0.16), topN: 200,
    allowedStrategies: ["covered_call", "cash_secured_put", "short_strangle", "iron_condor"] });
  const byType = (t) => res.candidates.filter((c) => c.strategyType === t);
  for (const t of ["covered_call", "cash_secured_put", "short_strangle"]) {
    const list = byType(t);
    assert.ok(list.length > 0, `no ${t}`);
    if (t !== "short_strangle") {
      assert.ok(list.some((c) => c.sizing.contractsSuggested > 0), `${t} sized to zero everywhere`);
    }
    for (const c of list) {
      assert.ok(c.sizing.stressLossPerUnit > 0 && c.sizing.stressLossPerUnit < c.sizing.capitalRequired + 1);
      assert.ok(["riskBudget", "buyingPower", "kelly"].includes(c.sizing.binding));
    }
  }
  const cc = byType("covered_call")[0];
  assert.equal(cc.sizing.riskBudgetExceeded, true);
  assert.ok(cc.sizing.stressLossPctOfAccount > 0.02);
  const csp = byType("cash_secured_put")[0];
  assert.match(csp.sizing.capitalBasis, /cash-secured/);
  assert.ok(csp.sizing.capitalRequired > csp.legs[0].strike * 90);
  const strangle = byType("short_strangle")[0];
  assert.match(strangle.sizing.capitalBasis, /Reg-T/);
  assert.equal(strangle.sizing.capitalApproximate, true);
  assert.equal(strangle.payoff.maxLoss, null);
  // a 2-sigma strangle loss on a $100 underlying is ~$700-1,200: zero lots is the
  // honest answer for a $25k/2% budget, and it sizes up on a larger account
  assert.equal(strangle.sizing.contractsSuggested, 0);
  assert.equal(strangle.sizing.binding, "riskBudget");
  const big = await screenWith({ data, mode: "vrp", forecaster: forecastOf(0.16), topN: 200, capital: 150_000,
    allowedStrategies: ["short_strangle"] });
  assert.ok(big.candidates.some((c) => c.sizing.contractsSuggested > 0));
});

test("v2 screen: definedRiskOnly drops undefined-risk structures; allowedStrategies honours v1 aliases", engineTest, async () => {
  const data = await realisticMarketData();
  const res = await screenWith({ data, mode: "market", definedRiskOnly: true, topN: 200 });
  assert.ok(res.candidates.every((c) => c.meta.risk !== "undefined"));
  assert.ok(!res.strategiesScreened.includes("short_strangle"));
  const v1 = await screenWith({ data, mode: "market", allowedStrategies: ["call_vertical"] });
  assert.ok(v1.candidates.every((c) => ["call_vertical", "put_credit_spread"].includes(c.strategyType)));
});

test("v2 screen: a missing forecast degrades honestly to market/directional mode with a warning", engineTest, async () => {
  const data = await realisticMarketData();
  const res = await screenWith({ data, mode: "both", directionalView: "neutral", forecaster: noForecast });
  assert.equal(res.mode, "market");
  assert.ok(res.warnings.some((w) => /forecast/i.test(w)));
  const res2 = await screenWith({ data, mode: "both", directionalView: "bearish", forecaster: noForecast });
  assert.equal(res2.mode, "directional");
  assert.equal(res2.forecast, null);
});

test("v2 screen: stale live data is warned about and point-in-time data is not", engineTest, async () => {
  const data = await realisticMarketData();
  const stale = await screenWith({ data: { ...data, stale: true, quoteAgeSeconds: 7200 }, mode: "market" });
  assert.ok(stale.warnings.some((w) => /minutes old/.test(w)));
  assert.ok(stale.candidates.every((c) => c.meta.marksQuality === "indicative"));
  const pit = await screenWith({ data: { ...data, pointInTime: true, asof: "2026-06-12", provider: "archive" }, mode: "market" });
  assert.ok(!pit.warnings.some((w) => /minutes old/.test(w)));
  assert.equal(pit.candidates[0].meta.asof, "2026-06-12");
  assert.equal(pit.candidates[0].meta.provider, "archive");
});

test("DEFAULTS expose the v2 knobs", () => {
  assert.equal(DEFAULTS.mode, "both");
  assert.equal(DEFAULTS.kellyFraction, 0.25);
  assert.equal(DEFAULTS.stressSigmas, 2);
});
