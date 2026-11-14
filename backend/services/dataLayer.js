// Market data layer: Python/yfinance adapter + 60s cache + liquidity gates.
//
// Constraints honored here (from the build brief):
// - every payload carries fetchedAt / dataAgeSeconds / stale — stale data is
//   flagged, never hidden
// - contracts failing volume/OI gates are REMOVED before anyone screens them;
//   contracts with a wide (or unknowable) spread are kept but flagged
//   `illiquid: true` and must never end up in a recommendation
const { runPython, PythonDomainError } = require("./pythonRun");

// Upstream/domain problem (unknown symbol, no options listed) — maps to 404.
class DataError extends Error {}

// Spread gate: a contract is illiquid when its dollar spread exceeds
// max(maxSpreadPct * mid, spreadFloorDollars). Pure percent-of-mid is the
// wrong measure — a $0.10-wide book on a $0.50 wing is the tightest book
// that wing will ever have, yet it is "20%".
const LIQUIDITY_GATES = {
  minVolume: 50,
  minOpenInterest: 100,
  maxSpreadPct: 0.05,
  spreadFloorDollars: 0.30,
};
const CACHE_TTL_MS = 60_000;
const STALE_AFTER_S = 15 * 60; // quotes older than this are flagged stale

const SYMBOL_RE = /^[A-Za-z][A-Za-z0-9.^-]{0,9}$/;

// Phase 1: every chain comes through the Python DataService (math/data/),
// which picks the provider (Massive, yfinance live; archive, DoltHub for a
// past `asof`) and stamps provider, asof, rates and dividendYield on the
// payload. Schema: docs/api-schema.md (v2).
function fetchViaPython(symbol, { maxExpirations = 6, minDte = 1, maxDte = 120,
                                  asof = null, provider = null, timeoutMs = 120_000 } = {}) {
  const req = {
    cmd: "chain", symbol, max_expirations: maxExpirations, min_dte: minDte, max_dte: maxDte,
  };
  if (asof) req.asof = asof;
  if (provider) req.provider = provider;
  if (process.env.OD_DATA_DIR) req.root = process.env.OD_DATA_DIR;
  return runPython("datasvc", req, { timeoutMs }).catch((err) => {
    throw err instanceof PythonDomainError ? new DataError(err.message) : err;
  });
}

function applyLiquidityGates(chains, gates = LIQUIDITY_GATES) {
  const out = {};
  const dropped = { noPrice: 0, volume: 0, openInterest: 0 };
  let total = 0;
  let kept = 0;
  for (const [exp, sides] of Object.entries(chains)) {
    out[exp] = { calls: [], puts: [] };
    for (const side of ["calls", "puts"]) {
      for (const contract of sides[side] || []) {
        total += 1;
        if (contract.mid === null || contract.mid <= 0) {
          dropped.noPrice += 1;
          continue;
        }
        // Phase 1: a source without volume/OI (DoltHub history) reports null.
        // Null is "unknown", not zero: keep the contract, flag it, and let the
        // caller decide (the backtester treats unknown as fine for marks, the
        // live screener never recommends it).
        const liquidityUnknown = contract.volume === null || contract.volume === undefined
          || contract.openInterest === null || contract.openInterest === undefined;
        if (!liquidityUnknown && contract.volume < gates.minVolume) {
          dropped.volume += 1;
          continue;
        }
        if (!liquidityUnknown && contract.openInterest < gates.minOpenInterest) {
          dropped.openInterest += 1;
          continue;
        }
        kept += 1;
        const hasBook = contract.bid > 0 && contract.ask >= contract.bid;
        const absSpread = hasBook
          ? Math.round((contract.ask - contract.bid) * 100) / 100
          : null;
        out[exp][side].push({
          ...contract,
          // wide book -> truly illiquid, never recommended while live;
          // missing book (market closed) -> indicative last-trade mark only
          illiquid: absSpread !== null
            && absSpread > Math.max(gates.maxSpreadPct * contract.mid, gates.spreadFloorDollars),
          indicativeOnly: !hasBook,
          liquidityUnknown,
        });
      }
    }
  }
  return { chains: out, stats: { total, kept, dropped } };
}

function createDataLayer({ fetcher = fetchViaPython, now = Date.now, ttlMs = CACHE_TTL_MS } = {}) {
  const cache = new Map(); // SYMBOL -> { at, data }

  function decorate(data) {
    const fetchAge = Math.max(0, Math.round((now() - Date.parse(data.fetchedAt)) / 1000));
    // staleness keys off the last actual trade in the chain when available:
    // a weekend fetch of Friday's closing book is fresh-by-fetch but stale-by-quote
    const lastTradeMs = data.lastTradeAt ? Date.parse(data.lastTradeAt) : NaN;
    const quoteAge = Number.isFinite(lastTradeMs)
      ? Math.max(0, Math.round((now() - lastTradeMs) / 1000))
      : null;
    const pointInTime = Boolean(data.asof) && !data.capabilities?.live;
    return {
      ...data,
      dataAgeSeconds: fetchAge,
      quoteAgeSeconds: quoteAge,
      pointInTime,
      // a historical chain is old on purpose; "stale" means live data went quiet
      stale: pointInTime ? false : (quoteAge ?? fetchAge) > STALE_AFTER_S,
    };
  }

  async function getMarketData(symbol, { refresh = false, maxExpirations = 6,
                                          minDte = 1, maxDte = 120, asof = null,
                                          provider = null } = {}) {
    if (typeof symbol !== "string" || !SYMBOL_RE.test(symbol.trim())) {
      throw new DataError(`invalid symbol: ${JSON.stringify(symbol)}`);
    }
    const sym = symbol.trim().toUpperCase();
    if (asof !== null && !/^\d{4}-\d{2}-\d{2}$/.test(String(asof))) {
      throw new DataError(`invalid asof date: ${JSON.stringify(asof)} (expected YYYY-MM-DD)`);
    }
    const key = `${sym}:${maxExpirations}:${minDte}:${maxDte}:${asof ?? "live"}:${provider ?? ""}`;
    const hit = cache.get(key);
    // point-in-time chains never change: cache them for the process lifetime
    if (hit && !refresh && (asof !== null || now() - hit.at < ttlMs)) {
      return decorate(hit.data);
    }
    const raw = await fetcher(sym, { maxExpirations, minDte, maxDte, asof, provider });
    const gated = applyLiquidityGates(raw.chains);
    const data = {
      ...raw,
      chains: gated.chains,
      liquidity: { gates: LIQUIDITY_GATES, ...gated.stats },
    };
    cache.set(key, { at: now(), data });
    return decorate(data);
  }

  return { getMarketData, cache };
}

const dataLayer = createDataLayer();

module.exports = {
  createDataLayer,
  applyLiquidityGates,
  fetchViaPython,
  dataLayer,
  DataError,
  LIQUIDITY_GATES,
  CACHE_TTL_MS,
  STALE_AFTER_S,
};
