// Detector v2 (Phase 4): fetch the chain, fetch a realized-vol forecast,
// hand both to the Python optimizer (math/optimizer.py) in ONE engine call,
// return the ranked candidates. Strike search, structures, densities,
// scoring and sizing all live in Python now; this module only resolves the
// inputs and keeps the HTTP contract (docs/api-schema.md) stable.
//
// Score v2 is EV-per-CVaR based and size-invariant; see
// docs/algorithms/optimizer-v2.md. The v1 table-driven mapping and
// POP-weighted score are gone (A1, A8-A13 in ROADMAP-v2.md).

const { callEngine } = require("./mathEngine");
const { dataLayer: defaultDataLayer } = require("./dataLayer");
const { runPython } = require("./pythonRun");

const DEFAULTS = {
  directionalView: "neutral",   // bullish | bearish | neutral | income (UI vocabulary)
  mode: "both",                 // market | vrp | directional | both
  conviction: 0.5,
  capital: 25_000,
  riskTolerancePct: 2,
  maxLossDollars: null,
  maxBuyingPowerPct: 50,
  kellyFraction: 0.25,
  stressSigmas: 2,
  minDTE: 5,
  maxDTE: 90,
  definedRiskOnly: false,
  allowedStrategies: [],
  maxExpirations: 6,
  topN: 20,
  forecastMethod: "har",
  costs: {},
  asof: null,                   // YYYY-MM-DD -> point-in-time screen (backtests)
};

// Score v2 weights, mirrored from optimizer.WEIGHTS for the recommender/UI.
const WEIGHTS = { evCvar: 0.40, pop: 0.15, theta: 0.15, liquidity: 0.15, confidence: 0.15 };

const FORECAST_TTL_MS = 60 * 60_000;

function ivBand(ivRank) {
  if (ivRank === null || ivRank === undefined) return "mid";
  if (ivRank >= 70) return "high";
  if (ivRank <= 30) return "low";
  return "mid";
}

// Default forecaster: datasvc forecast at the median DTE of the window, cached
// per symbol/horizon/method for an hour. Any failure -> null (the optimizer
// then screens in market or directional mode and says so in warnings).
function createForecaster({ run = runPython, now = Date.now } = {}) {
  const cache = new Map();
  return async function forecast(symbol, horizonDays, method, ivAtm) {
    const key = `${symbol}:${horizonDays}:${method}`;
    const hit = cache.get(key);
    if (hit && now() - hit.at < FORECAST_TTL_MS) return hit.value;
    try {
      const req = { cmd: "forecast", symbol, horizon: horizonDays, method, iv_atm: ivAtm ?? null };
      if (process.env.OD_DATA_DIR) req.root = process.env.OD_DATA_DIR;
      const value = await run("datasvc", req, { timeoutMs: 120_000 });
      cache.set(key, { at: now(), value });
      return value;
    } catch (err) {
      cache.set(key, { at: now(), value: null, error: String(err.message || err) });
      return null;
    }
  };
}

function createDetector({
  dataLayer = defaultDataLayer, engine = callEngine, forecaster = createForecaster(), now = Date.now,
} = {}) {
  async function screen(params = {}) {
    const opts = { ...DEFAULTS, ...params };
    if (!opts.symbol) throw new Error("symbol is required");
    opts.topN = Math.min(Math.max(1, Math.floor(opts.topN)), 200);

    const data = await dataLayer.getMarketData(opts.symbol, {
      refresh: Boolean(opts.refresh),
      maxExpirations: opts.maxExpirations,
      minDte: opts.minDTE,
      maxDte: opts.maxDTE,
      asof: opts.asof ?? null,
    });

    const view = ["bullish", "bearish", "neutral", "income"].includes(opts.directionalView)
      ? opts.directionalView : "neutral";
    const needsForecast = opts.mode === "vrp" || opts.mode === "both";
    let forecast = null;
    if (needsForecast) {
      // trading-day horizon ~ 0.7 x calendar days of the window midpoint
      const horizon = Math.max(5, Math.round(((opts.minDTE + opts.maxDTE) / 2) * 0.7));
      forecast = await forecaster(data.symbol, horizon, opts.forecastMethod, data.atmIv30 ?? data.atmIvModel ?? null);
    }

    const options = {
      mode: opts.mode, view, conviction: opts.conviction, capital: opts.capital,
      riskTolerancePct: opts.riskTolerancePct, maxLossDollars: opts.maxLossDollars,
      maxBuyingPowerPct: opts.maxBuyingPowerPct, kellyFraction: opts.kellyFraction,
      stressSigmas: opts.stressSigmas, minDTE: opts.minDTE, maxDTE: opts.maxDTE,
      definedRiskOnly: Boolean(opts.definedRiskOnly), allowedStrategies: opts.allowedStrategies,
      topN: opts.topN, costs: opts.costs,
    };
    const result = await engine("screen_v2", { payload: data, options, forecast });

    const warnings = [...(result.warnings || [])];
    if (data.stale && !data.pointInTime) {
      const ageMin = Math.round((data.quoteAgeSeconds ?? data.dataAgeSeconds) / 60);
      warnings.unshift(`quotes are ~${ageMin} minutes old (market closed?). Candidates priced off closing marks; verify live spreads before trading`);
    }
    if (needsForecast && !forecast) {
      warnings.push("realized-vol forecast unavailable (no price history reachable); VRP not applied");
    }
    return {
      ...result,
      ivBand: ivBand(data.ivRank),
      forecast: forecast ? {
        method: forecast.method, sigma: forecast.sigma, sigmaLo: forecast.sigmaLo ?? null,
        sigmaHi: forecast.sigmaHi ?? null, horizon: forecast.horizon, asOf: forecast.asOf ?? null,
        vrp: forecast.vrp ?? null,
      } : null,
      warnings,
    };
  }

  return { screen };
}

const detector = createDetector();

module.exports = { createDetector, createForecaster, detector, DEFAULTS, WEIGHTS, ivBand };
