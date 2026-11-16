// Research (Phase 5 simulator):
//   GET  /research/strategies            -> plug-in list with params and docs
//   GET  /research/runs                  -> saved results (summary)
//   POST /research/backtest              -> { strategy, params, config } run now (minutes)
//   POST /research/walkforward           -> { strategy, grid, config, trainDays, testDays, objective }
//   GET  /research/forecast-walkforward/:symbol?horizon=21
// Long jobs run as one od-math simsvc process; one at a time per backend.
const express = require("express");

const { runPython, PythonDomainError } = require("../services/pythonRun");

const router = express.Router();
const LONG_MS = 20 * 60_000;
let running = null;

function root() {
  return process.env.OD_DATA_DIR ? { root: process.env.OD_DATA_DIR } : {};
}

function fail(res, err) {
  if (err instanceof PythonDomainError) {
    res.status(400).json({ error: err.message });
    return;
  }
  res.status(502).json({ error: String(err.message || err) });
}

async function exclusive(res, job) {
  if (running) {
    res.status(409).json({ error: "a research job is already running; wait for it to finish" });
    return;
  }
  running = job();
  try {
    res.json(await running);
  } catch (err) {
    fail(res, err);
  } finally {
    running = null;
  }
}

router.get("/strategies", async (_req, res) => {
  try {
    res.json(await runPython("simsvc", { cmd: "strategies", ...root() }, { timeoutMs: 60_000 }));
  } catch (err) {
    fail(res, err);
  }
});

router.get("/runs", async (_req, res) => {
  try {
    res.json(await runPython("simsvc", { cmd: "runs", ...root() }, { timeoutMs: 60_000 }));
  } catch (err) {
    fail(res, err);
  }
});

router.get("/status", (_req, res) => res.json({ running: running !== null }));

router.post("/backtest", (req, res) => {
  const { strategy, params, config } = req.body || {};
  if (typeof strategy !== "string" || !config || typeof config.symbol !== "string") {
    res.status(400).json({ error: "strategy and config.symbol are required" });
    return;
  }
  exclusive(res, () => runPython("simsvc", {
    cmd: "run", strategy, params: params || {}, config, ...root(),
  }, { timeoutMs: LONG_MS }));
});

router.post("/walkforward", (req, res) => {
  const { strategy, grid, config, trainDays, testDays, objective } = req.body || {};
  if (typeof strategy !== "string" || !config || typeof config.symbol !== "string") {
    res.status(400).json({ error: "strategy and config.symbol are required" });
    return;
  }
  exclusive(res, () => runPython("simsvc", {
    cmd: "walkforward", strategy, grid: Array.isArray(grid) ? grid : [{}], config,
    trainDays: trainDays ?? 250, testDays: testDays ?? 60, objective: objective ?? "sharpe", ...root(),
  }, { timeoutMs: LONG_MS }));
});

router.get("/forecast-walkforward/:symbol", (req, res) => {
  const horizon = Math.min(Math.max(Number.parseInt(req.query.horizon, 10) || 21, 5), 126);
  exclusive(res, () => runPython("simsvc", {
    cmd: "forecast_walkforward", symbol: req.params.symbol, horizon, ...root(),
  }, { timeoutMs: LONG_MS }));
});

module.exports = router;
