// Market data (Phase 1 data platform):
//   GET  /data/providers                 -> provider order, capabilities, rates source, archive coverage
//   PUT  /data/providers                 -> { massiveApiKey?, liveOrder?, historicalOrder? }
//   POST /data/rates/refresh             -> re-fetch the Treasury curve
//   GET  /data/:symbol[?refresh=1&expirations=6&asof=YYYY-MM-DD&provider=name]
//        -> normalized chain after liquidity gates; `asof` = point-in-time read
const { Router } = require("express");
const { dataLayer, DataError } = require("../services/dataLayer");
const { runPython, PythonDomainError } = require("../services/pythonRun");

const router = Router();

function root() {
  return process.env.OD_DATA_DIR ? { root: process.env.OD_DATA_DIR } : {};
}

function fail(res, err) {
  if (err instanceof DataError || err instanceof PythonDomainError) {
    res.status(404).json({ error: err.message });
    return;
  }
  res.status(502).json({ error: String(err.message || err) });
}

router.get("/providers", async (_req, res) => {
  try {
    res.json(await runPython("datasvc", { cmd: "status", ...root() }, { timeoutMs: 60_000 }));
  } catch (err) {
    fail(res, err);
  }
});

router.put("/providers", async (req, res) => {
  const body = req.body || {};
  const patch = {};
  if (typeof body.massiveApiKey === "string") patch.massiveApiKey = body.massiveApiKey.trim() || null;
  for (const key of ["liveOrder", "historicalOrder"]) {
    if (Array.isArray(body[key])) patch[key] = body[key].map(String);
  }
  try {
    const status = await runPython("datasvc", { cmd: "config", patch, ...root() }, { timeoutMs: 60_000 });
    dataLayer.cache.clear(); // provider change invalidates cached live chains
    res.json(status);
  } catch (err) {
    fail(res, err);
  }
});

router.post("/rates/refresh", async (_req, res) => {
  try {
    res.json(await runPython("datasvc", { cmd: "rates", force: true, ...root() }, { timeoutMs: 60_000 }));
  } catch (err) {
    fail(res, err);
  }
});

router.get("/:symbol", async (req, res) => {
  try {
    const maxExpirations = Math.min(Number.parseInt(req.query.expirations, 10) || 6, 12);
    const data = await dataLayer.getMarketData(req.params.symbol, {
      refresh: req.query.refresh === "1",
      maxExpirations,
      asof: typeof req.query.asof === "string" && req.query.asof !== "" ? req.query.asof : null,
      provider: typeof req.query.provider === "string" && req.query.provider !== "" ? req.query.provider : null,
    });
    res.json(data);
  } catch (err) {
    fail(res, err);
  }
});

module.exports = router;
