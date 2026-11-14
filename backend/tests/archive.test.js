const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  createArchiveService, journalSymbolsFrom, tradingDateFor, DEFAULT_WATCHLIST,
} = require("../services/archive");

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "od-archive-"));
}

// 2026-11-13 is a Friday. Timestamps are ET expressed as UTC (ET = UTC-5 in November).
const FRI_AFTER_CLOSE = Date.parse("2026-11-13T22:00:00Z"); // 17:00 ET
const FRI_BEFORE_CLOSE = Date.parse("2026-11-13T15:00:00Z"); // 10:00 ET
const SAT_NOON = Date.parse("2026-11-14T17:00:00Z");
const MON_PRE_OPEN = Date.parse("2026-11-16T14:00:00Z"); // 09:00 ET

test("tradingDateFor matches the Python rule (close, weekend, pre-open)", () => {
  assert.equal(tradingDateFor(FRI_AFTER_CLOSE), "2026-11-13");
  assert.equal(tradingDateFor(FRI_BEFORE_CLOSE), "2026-11-12");
  assert.equal(tradingDateFor(SAT_NOON), "2026-11-13");
  assert.equal(tradingDateFor(MON_PRE_OPEN), "2026-11-13");
  assert.equal(tradingDateFor(Date.parse("2026-11-16T21:00:00Z")), "2026-11-16"); // Mon 16:00 ET
});

test("tick runs only for missing symbols and marks files as done", async () => {
  const dir = tmpDir();
  const calls = [];
  const svc = createArchiveService({
    dataDir: dir,
    now: () => FRI_AFTER_CLOSE,
    log: { warn() {}, error() {} },
    run: async (req) => {
      calls.push(req);
      for (const s of req.symbols) {
        fs.mkdirSync(path.join(req.root, s), { recursive: true });
        fs.writeFileSync(path.join(req.root, s, "2026-11-13.parquet"), "x");
      }
      return { date: "2026-11-13", written: req.symbols.map((s) => ({ symbol: s, rows: 1 })), skipped: [], errors: {} };
    },
  });
  // pre-seed SPY so only the rest are requested
  fs.mkdirSync(path.join(svc.root, "SPY"), { recursive: true });
  fs.writeFileSync(path.join(svc.root, "SPY", "2026-11-13.parquet"), "x");

  const r1 = await svc.tick();
  assert.equal(r1.ok, true);
  assert.deepEqual(calls[0].symbols, DEFAULT_WATCHLIST.filter((s) => s !== "SPY"));
  assert.equal(calls[0].max_expirations, 12);

  const r2 = await svc.tick();
  assert.deepEqual(r2, { skipped: "complete", date: "2026-11-13" });
  assert.equal(calls.length, 1);
});

test("disabled setting stops the scheduler; settings persist and normalise", async () => {
  const dir = tmpDir();
  let ran = 0;
  const svc = createArchiveService({
    dataDir: dir, now: () => FRI_AFTER_CLOSE, run: async () => { ran += 1; return { written: [], skipped: [], errors: {} }; },
  });
  const saved = svc.saveSettings({ enabled: false, extraSymbols: [" vgt", "vgt", "xle "] });
  assert.deepEqual(saved.extraSymbols, ["VGT", "XLE"]);
  assert.equal(saved.enabled, false);
  assert.deepEqual(await svc.tick(), { skipped: "disabled" });
  assert.equal(ran, 0);
  assert.ok(svc.watchlist().includes("VGT"));
  // a fresh service over the same dir reads the persisted file
  const again = createArchiveService({ dataDir: dir, run: async () => ({}) });
  assert.equal(again.loadSettings().enabled, false);
});

test("snapshot never runs twice concurrently and records failures", async () => {
  const dir = tmpDir();
  let inflight = 0;
  let max = 0;
  const svc = createArchiveService({
    dataDir: dir,
    now: () => FRI_AFTER_CLOSE,
    log: { warn() {}, error() {} },
    run: () => new Promise((resolve, reject) => {
      inflight += 1;
      max = Math.max(max, inflight);
      setTimeout(() => { inflight -= 1; reject(new Error("yfinance down")); }, 10);
    }),
  });
  const [a, b] = await Promise.all([svc.snapshot(), svc.snapshot()]);
  assert.equal(max, 1);
  assert.equal(a, b);
  assert.equal(a.ok, false);
  assert.match(a.error, /yfinance down/);
});

test("journalSymbolsFrom reads every account and skips closed/trashed trades", () => {
  const dir = tmpDir();
  fs.mkdirSync(path.join(dir, "accounts", "u1"), { recursive: true });
  fs.writeFileSync(path.join(dir, "trades.json"), JSON.stringify([
    { symbol: "aapl", status: "open" }, { symbol: "MSFT", status: "closed" },
  ]));
  fs.writeFileSync(path.join(dir, "accounts", "u1", "trades.json"), JSON.stringify([
    { symbol: "VGT", status: "open" }, { symbol: "XLE", status: "open", deletedAt: "x" }, { symbol: "TSLA", archived: true },
  ]));
  fs.writeFileSync(path.join(dir, "accounts", "u1", "garbage"), "not json");
  assert.deepEqual(journalSymbolsFrom(dir).sort(), ["AAPL", "VGT"]);
  assert.deepEqual(journalSymbolsFrom(path.join(dir, "nope")), []);
});
