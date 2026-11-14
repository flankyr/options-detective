// Calculator: full analysis for one position (a Detector candidate or
// user-adjusted legs) — per-leg greeks, net greeks, exact payoff summary,
// payoff curve, probabilities, sizing. One warm engine batch per call, so
// adjustments recalculate in tens of milliseconds.

const { callEngineBatch } = require("./mathEngine");
const {
  capitalRequiredOf, engineLegs, netGreeksOf, totalDebitOf,
} = require("./positionMath");

// Bad request-shaped input (missing legs, nonsense numbers) — maps to 400.
class CalcInputError extends Error {}

const LEG_TYPES = new Set([
  "long_call", "short_call", "long_put", "short_put", "long_stock", "short_stock",
]);

const DEFAULTS = {
  riskFreeRate: Number(process.env.RISK_FREE_RATE ?? 0.04),
  dividendYield: 0, // Phase 2: continuous yield q for BSM pricing/greeks
  // Phase 3: distribution-based evaluation. `smile` (a serialized smile from
  // the chain payload) gives the market-implied density; without it the
  // lognormal at `sigma` is used. mode: market | vrp | directional | both.
  smile: null,
  mode: "market",
  sigmaForecast: null,
  view: "neutral",
  conviction: 0,
  costs: 0,
  capital: 25_000,
  riskTolerancePct: 2,
  maxLossDollars: null,
  curvePoints: 81,
};

function validateInputs({ legs, spot, dte, sigma }) {
  if (!Array.isArray(legs) || legs.length === 0 || legs.length > 6) {
    throw new CalcInputError("legs must be an array of 1-6 legs");
  }
  legs.forEach((leg, i) => {
    if (!leg || typeof leg !== "object" || !LEG_TYPES.has(leg.type)) {
      throw new CalcInputError(`leg ${i}: type must be one of ${[...LEG_TYPES].join(", ")}`);
    }
    if (!leg.type.endsWith("stock")
        && !(Number.isFinite(leg.strike) && leg.strike > 0)) {
      throw new CalcInputError(`leg ${i}: option legs need a positive strike`);
    }
    if (!(Number.isFinite(leg.price) && leg.price >= 0)) {
      throw new CalcInputError(`leg ${i}: price must be >= 0`);
    }
    if (!(Number.isInteger(leg.qty) && leg.qty > 0)) {
      throw new CalcInputError(`leg ${i}: qty must be a positive integer`);
    }
  });
  if (!(Number.isFinite(spot) && spot > 0)) {
    throw new CalcInputError("spot (current underlying price) must be > 0");
  }
  if (!(Number.isFinite(dte) && dte > 0)) {
    throw new CalcInputError("dte (days to expiry) must be > 0");
  }
  if (sigma !== undefined && sigma !== null
      && !(Number.isFinite(sigma) && sigma > 0)) {
    throw new CalcInputError("sigma must be > 0 when provided");
  }
}

function validateYield(q) {
  if (q === undefined || q === null) return 0;
  if (!(Number.isFinite(q) && q >= 0 && q < 1)) {
    throw new CalcInputError("dividendYield must be a decimal in [0, 1)");
  }
  return q;
}

function resolveSigma(sigma, legs) {
  if (Number.isFinite(sigma) && sigma > 0) return sigma;
  const ivs = legs.map((l) => l.iv).filter((v) => Number.isFinite(v) && v > 0);
  if (ivs.length === 0) {
    throw new CalcInputError(
      "sigma is required (none provided and no leg carries an implied volatility)");
  }
  return ivs.reduce((a, b) => a + b, 0) / ivs.length;
}

function createCalculator({ engineBatch = callEngineBatch } = {}) {
  async function analyze(params = {}) {
    const opts = { ...DEFAULTS, ...params };
    validateInputs(opts);
    const sigma = resolveSigma(opts.sigma, opts.legs);
    const q = validateYield(opts.dividendYield);
    const T = opts.dte / 365;

    // Strike adjustments have no market quote to lean on; reprice the legs
    // at Black-Scholes theoretical value (each leg's own IV) and label them.
    if (opts.repriceTheoretical) {
      const priceRequests = opts.legs
        .filter((leg) => !leg.type.endsWith("stock"))
        .map((leg) => ({
          fn: "bsm_price",
          args: {
            S: opts.spot, K: leg.strike, T, r: opts.riskFreeRate, q,
            sigma: (Number.isFinite(leg.iv) && leg.iv > 0) ? leg.iv : sigma,
            option_type: leg.type.endsWith("call") ? "call" : "put",
          },
        }));
      const priced = await engineBatch(priceRequests);
      let pi = 0;
      opts.legs = opts.legs.map((leg) => {
        if (leg.type.endsWith("stock")) return { ...leg, price: opts.spot };
        const item = priced[pi];
        pi += 1;
        if (!item.ok) throw new CalcInputError(item.error);
        return { ...leg, price: Math.round(item.result * 100) / 100, theoretical: true };
      });
    }

    const eLegs = engineLegs(opts.legs);
    const probArgs = {
      legs: eLegs, current_price: opts.spot, T, sigma, r: opts.riskFreeRate,
    };

    const evalArgs = {
      legs: eLegs, S: opts.spot, T, r: opts.riskFreeRate, q,
      smile: opts.smile ?? null, sigma, mode: opts.mode ?? "market",
      sigma_forecast: Number.isFinite(opts.sigmaForecast) && opts.sigmaForecast > 0 ? opts.sigmaForecast : null,
      view: opts.view ?? "neutral", conviction: Number.isFinite(opts.conviction) ? opts.conviction : 0,
      costs: Number.isFinite(opts.costs) ? opts.costs : 0,
    };
    const requests = [
      { fn: "payoff_summary", args: { legs: eLegs } },
      { fn: "payoff_curve", args: { legs: eLegs, current_price: opts.spot, num_points: opts.curvePoints } },
      { fn: "prob_of_profit", args: probArgs },
      { fn: "prob_max_profit", args: probArgs },
      ...opts.legs.map((leg) => (leg.type.endsWith("stock")
        ? null
        : {
          fn: "bsm_greeks",
          args: {
            S: opts.spot, K: leg.strike, T, r: opts.riskFreeRate, q,
            sigma: (Number.isFinite(leg.iv) && leg.iv > 0) ? leg.iv : sigma,
            option_type: leg.type.endsWith("call") ? "call" : "put",
          },
        })).filter(Boolean),
      { fn: "evaluate_position", args: evalArgs },
    ];
    const results = await engineBatch(requests);

    const [summary, curve, pop, pmp] = results;
    for (const item of [summary, curve, pop, pmp]) {
      if (!item.ok) throw new CalcInputError(item.error);
    }
    const evaluation = results[results.length - 1];
    const greekResults = results.slice(4, results.length - 1);
    let gi = 0;
    const legGreeks = opts.legs.map((leg) => {
      if (leg.type.endsWith("stock")) return null;
      const r = greekResults[gi];
      gi += 1;
      return r.ok ? r.result : null;
    });

    const maxProfit = summary.result.max_profit; // null = unbounded
    const maxLoss = summary.result.max_loss;
    const totalDebit = totalDebitOf(opts.legs);
    const netGreeks = netGreeksOf(opts.legs, legGreeks);
    const capReq = capitalRequiredOf(
      opts.strategyType ?? null, opts.legs, maxLoss ?? Infinity, opts.spot, totalDebit);

    const budget = Math.min(
      opts.capital * (opts.riskTolerancePct / 100),
      opts.maxLossDollars ?? Infinity,
    );
    const contracts = maxLoss !== null && maxLoss > 0 ? Math.floor(budget / maxLoss) : 0;

    return {
      legs: opts.legs.map((leg, i) => ({ ...leg, greeks: legGreeks[i] ?? undefined })),
      // Phase 3: EV / POP / CVaR under the market-implied (or lognormal) density
      // and, when a mode is set, under the scenario density. Absent only if the
      // engine rejected the inputs; the reason travels with it.
      evaluation: evaluation.ok ? evaluation.result : { error: evaluation.error },
      netGreeks,
      payoff: {
        maxProfit,
        maxLoss,
        breakevens: summary.result.breakevens,
        profitAtExpiry: curve.result,
      },
      probability: { pop: pop.result, probMaxProfit: pmp.result },
      metrics: {
        riskRewardRatio: maxProfit !== null && maxLoss !== null && maxLoss > 0
          ? Math.round((maxProfit / maxLoss) * 100) / 100 : null,
        capitalEfficiency: maxProfit !== null && capReq.amount > 0
          ? Math.round((maxProfit / capReq.amount) * 1000) / 1000 : null,
        thetaPerDay: netGreeks.theta,
      },
      sizing: {
        contractsSuggested: contracts,
        totalDebit,
        pctOfAccount: Math.round((Math.abs(totalDebit) * Math.max(contracts, 1) / opts.capital) * 1000) / 1000,
        capitalRequired: Math.round(capReq.amount * 100) / 100,
        capitalApproximate: capReq.approximate,
      },
      inputs: {
        spot: opts.spot, dte: opts.dte, sigma, riskFreeRate: opts.riskFreeRate, dividendYield: q,
        capital: opts.capital, riskTolerancePct: opts.riskTolerancePct,
        strategyType: opts.strategyType ?? null,
      },
    };
  }

  return { analyze };
}

const calculator = createCalculator();

module.exports = { createCalculator, calculator, CalcInputError };
