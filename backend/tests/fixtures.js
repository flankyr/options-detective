// Synthetic, liquidity-gated market data for offline Detector tests.
// Crude but well-behaved marks: intrinsic + a bell-shaped time value around
// ATM, so debit spreads always cost something and condors always collect.

function timeValue(strike, spot) {
  return 8 * Math.exp(-(((strike - spot) / 15) ** 2));
}

function contract(strike, mid, iv) {
  return {
    strike,
    bid: Math.round((mid - 0.05) * 100) / 100,
    ask: Math.round((mid + 0.05) * 100) / 100,
    mid: Math.round(mid * 100) / 100,
    volume: 600,
    openInterest: 2000,
    impliedVolatility: iv,
    spreadPct: 0.02,
    timestamp: null,
    illiquid: false,
  };
}

function syntheticChain({ spot = 100, iv = 0.25, strikes } = {}) {
  const ks = strikes
    || Array.from({ length: 13 }, (_, i) => 70 + i * 5); // 70..130 step 5
  return {
    calls: ks.map((k) => contract(k, Math.max(spot - k, 0) + timeValue(k, spot), iv)),
    puts: ks.map((k) => contract(k, Math.max(k - spot, 0) + timeValue(k, spot), iv)),
  };
}

function isoDatePlusDays(days) {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

function syntheticMarketData({ spot = 100, iv = 0.25, ivRank = 80, dtes = [30, 60] } = {}) {
  const expirations = dtes.map(isoDatePlusDays);
  const chains = {};
  for (const exp of expirations) chains[exp] = syntheticChain({ spot, iv });
  return {
    symbol: "TEST",
    price: spot,
    atmIv: iv,
    ivRank,
    ivRankMethod: "fixture",
    expirations,
    chains,
    fetchedAt: new Date().toISOString(),
    dataAgeSeconds: 0,
    stale: false,
    liquidity: { gates: {}, total: 0, kept: 0, dropped: {} },
  };
}

function fakeDataLayer(data) {
  return { getMarketData: async () => data };
}

module.exports = { syntheticChain, syntheticMarketData, fakeDataLayer, isoDatePlusDays };

// ---- Phase 4: a realistic chain priced by the engine (BSM + mild put skew) ----
// Needs the Python engine. Vendor IV == model IV, books 10c wide, deep OI.
async function realisticMarketData({ spot = 100, atmIv = 0.22, skew = -0.15, dtes = [30, 60],
                                     r = 0.04, q = 0.01, strikeStep = 2.5, span = 0.3 } = {}) {
  const { callEngineBatch } = require("../services/mathEngine");
  const expirations = dtes.map(isoDatePlusDays);
  const asof = new Date().toISOString().slice(0, 10);
  const strikes = [];
  for (let k = Math.round((spot * (1 - span)) / strikeStep) * strikeStep; k <= spot * (1 + span); k += strikeStep) strikes.push(k);
  const reqs = [];
  const index = [];
  dtes.forEach((dte, ei) => {
    const T = dte / 365;
    const F = spot * Math.exp((r - q) * T);
    for (const K of strikes) {
      const kk = Math.log(K / F);
      const iv = Math.max(0.05, atmIv + skew * kk + 0.3 * kk * kk);
      for (const type of ["call", "put"]) {
        index.push({ ei, K, type, iv });
        reqs.push({ fn: "bsm_price", args: { S: spot, K, T, r, sigma: iv, q, option_type: type } });
      }
    }
  });
  const prices = await callEngineBatch(reqs);
  const chains = {};
  const rates = {};
  expirations.forEach((e) => { chains[e] = { calls: [], puts: [] }; rates[e] = r; });
  index.forEach(({ ei, K, type, iv }, i) => {
    const mid = Math.max(prices[i].result, 0.01);
    const half = Math.max(0.05, mid * 0.01);
    chains[expirations[ei]][type === "call" ? "calls" : "puts"].push({
      strike: K, bid: Math.round((mid - half) * 100) / 100, ask: Math.round((mid + half) * 100) / 100,
      mid: Math.round(mid * 10000) / 10000, volume: 800, openInterest: 4000,
      impliedVolatility: iv, ivVendor: iv, spreadPct: Math.round((2 * half / mid) * 10000) / 10000,
      timestamp: null, illiquid: false, indicativeOnly: false,
    });
  });
  return {
    symbol: "TEST", price: spot, atmIv, ivRank: 55, expirations, chains, rates, dividendYield: q,
    asof, provider: "fixture", fetchedAt: new Date().toISOString(), lastTradeAt: new Date().toISOString(),
    dataAgeSeconds: 10, quoteAgeSeconds: 10, stale: false, pointInTime: false, exerciseStyle: "american",
    capabilities: { live: true, historical: false },
  };
}

module.exports.realisticMarketData = realisticMarketData;
