// Chain archive scheduler (Phase 0, roadmap task 8).
//
// The Python side (math/archive.py) writes one parquet file per symbol per
// trading day. This module decides WHEN to run it and WHICH symbols:
//   - on backend start, and then every CHECK_INTERVAL_MS;
//   - only after the 16:00 ET close (or on a weekend / before the next open),
//     when today's trading-date file is missing for any watchlist symbol;
//   - watchlist = DEFAULT_WATCHLIST + every symbol in the journal, de-duplicated;
//   - never twice concurrently; failures are logged and retried next tick.
// Settings persist in <OD_DATA_DIR>/archive-settings.json (shared across
// accounts: the archive is market data, not user data).
const fs = require("fs");
const path = require("path");

const { runPython } = require("./pythonRun");

const DEFAULT_WATCHLIST = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"];
const CHECK_INTERVAL_MS = 60 * 60_000;
const DATA_DIR = process.env.OD_DATA_DIR || path.join(__dirname, "..", "data");

// Mirrors archive.trading_date_for() in Python: the session a snapshot taken
// at `nowMs` describes. Kept in both languages so the Node side can decide
// "is today's file already there" without spawning Python.
function tradingDateFor(nowMs) {
  const et = new Date(new Date(nowMs).toLocaleString("en-US", { timeZone: "America/New_York" }));
  const d = new Date(Date.UTC(et.getFullYear(), et.getMonth(), et.getDate()));
  if (et.getHours() < 16) d.setUTCDate(d.getUTCDate() - 1);
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

function createArchiveService({
  dataDir = DATA_DIR,
  run = (req) => runPython("archive", req, { timeoutMs: 15 * 60_000 }),
  journalSymbols = () => [],
  now = Date.now,
  log = console,
} = {}) {
  const settingsFile = path.join(dataDir, "archive-settings.json");
  const root = process.env.OD_ARCHIVE_DIR || path.join(dataDir, "archive");
  let running = null;
  let timer = null;
  let last = null; // last run result or error, for the UI

  function loadSettings() {
    try {
      const s = JSON.parse(fs.readFileSync(settingsFile, "utf8"));
      return {
        enabled: s.enabled !== false,
        extraSymbols: Array.isArray(s.extraSymbols) ? s.extraSymbols : [],
        maxExpirations: Number.isInteger(s.maxExpirations) ? s.maxExpirations : 12,
      };
    } catch {
      return { enabled: true, extraSymbols: [], maxExpirations: 12 };
    }
  }

  function saveSettings(patch) {
    const next = { ...loadSettings(), ...patch };
    if (Array.isArray(next.extraSymbols)) {
      next.extraSymbols = [...new Set(next.extraSymbols
        .map((s) => String(s).trim().toUpperCase()).filter(Boolean))];
    }
    fs.mkdirSync(dataDir, { recursive: true });
    const tmp = `${settingsFile}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2));
    fs.renameSync(tmp, settingsFile);
    return next;
  }

  function watchlist() {
    const s = loadSettings();
    const all = [...DEFAULT_WATCHLIST, ...s.extraSymbols, ...journalSymbols()]
      .map((x) => String(x).trim().toUpperCase()).filter(Boolean);
    return [...new Set(all)];
  }

  function hasSnapshot(symbol, date) {
    return fs.existsSync(path.join(root, symbol, `${date}.parquet`));
  }

  function missingFor(date) {
    return watchlist().filter((s) => !hasSnapshot(s, date));
  }

  async function snapshot({ force = false, symbols } = {}) {
    if (running) return running;
    const s = loadSettings();
    const syms = symbols && symbols.length ? symbols : watchlist();
    running = run({ symbols: syms, root, force, max_expirations: s.maxExpirations })
      .then((result) => {
        last = { at: new Date(now()).toISOString(), ok: true, ...result };
        if (Object.keys(result.errors || {}).length) {
          log.warn("[archive] partial:", result.errors);
        }
        return last;
      })
      .catch((err) => {
        last = { at: new Date(now()).toISOString(), ok: false, error: String(err.message || err) };
        log.error("[archive] failed:", err.message);
        return last;
      })
      .finally(() => { running = null; });
    return running;
  }

  // One scheduler tick: run only if enabled and something is missing.
  async function tick() {
    if (!loadSettings().enabled) return { skipped: "disabled" };
    const date = tradingDateFor(now());
    const missing = missingFor(date);
    if (missing.length === 0) return { skipped: "complete", date };
    return snapshot({ symbols: missing });
  }

  function start() {
    if (timer) return;
    // first tick shortly after boot so the app window is not delayed
    setTimeout(() => tick().catch(() => {}), 15_000).unref();
    timer = setInterval(() => tick().catch(() => {}), CHECK_INTERVAL_MS);
    timer.unref();
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  async function status() {
    const date = tradingDateFor(now());
    let symbols = {};
    try {
      symbols = (await run({ status: true, root, symbols: watchlist() })).symbols;
    } catch (err) {
      log.warn("[archive] status unavailable:", err.message);
    }
    return {
      settings: loadSettings(),
      root,
      watchlist: watchlist(),
      tradingDate: date,
      missingToday: missingFor(date),
      running: running !== null,
      last,
      symbols,
    };
  }

  return { start, stop, tick, snapshot, status, saveSettings, loadSettings, watchlist, root };
}

// Symbols of open journal trades across every local account, so the archive
// covers what the user actually trades. Tolerant reader: a missing or
// malformed journal just contributes nothing.
function journalSymbolsFrom(dataDir) {
  const files = [path.join(dataDir, "trades.json")];
  const accountsDir = path.join(dataDir, "accounts");
  try {
    for (const id of fs.readdirSync(accountsDir)) {
      files.push(path.join(accountsDir, id, "trades.json"));
    }
  } catch { /* no accounts dir yet */ }
  const out = new Set();
  for (const f of files) {
    try {
      for (const t of JSON.parse(fs.readFileSync(f, "utf8"))) {
        if (t && t.symbol && !t.deletedAt && !t.archived && t.status !== "closed") {
          out.add(String(t.symbol).toUpperCase());
        }
      }
    } catch { /* absent or unreadable journal */ }
  }
  return [...out];
}

const archiveService = createArchiveService({
  journalSymbols: () => journalSymbolsFrom(DATA_DIR),
});

module.exports = {
  createArchiveService, archiveService, journalSymbolsFrom, tradingDateFor, DEFAULT_WATCHLIST,
};
