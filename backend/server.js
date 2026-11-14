const express = require("express");
const cors = require("cors");

const detectRouter = require("./routes/detect");
const calculateRouter = require("./routes/calculate");
const recommendRouter = require("./routes/recommend");
const dataRouter = require("./routes/data");
const journalRouter = require("./routes/journal");
const paperRouter = require("./routes/paper");
const etfRouter = require("./routes/etf");
const screenerRouter = require("./routes/screener");
const marketRouter = require("./routes/market");
const { router: authRouter, requireAccount } = require("./routes/auth");
const accountRouter = require("./routes/account");
const archiveRouter = require("./routes/archive");
const { archiveService } = require("./services/archive");
const { callEngine, engineClient } = require("./services/mathEngine");

const app = express();

// I1 (roadmap v2): the backend is a private helper for the Electron renderer.
// It listens on loopback only (see app.listen below) and answers CORS for the
// renderer's own origins, so neither another machine on the LAN nor a web
// page open in a browser can drive /detect or /calculate. Electron's packaged
// renderer loads from file:// and sends no Origin header, which is why a
// missing origin is accepted; browsers always send one.
const ALLOWED_ORIGINS = new Set([
  "http://localhost:5173", "http://127.0.0.1:5173", // vite dev
  "http://localhost:5174", "http://127.0.0.1:5174", // isolated dev (backend-alt)
  ...(process.env.OD_CORS_ORIGINS ? process.env.OD_CORS_ORIGINS.split(",") : []),
]);
app.use(cors({
  origin: (origin, cb) => cb(null, !origin || ALLOWED_ORIGINS.has(origin)),
}));
// 10mb: account backups (v1.7.2 import) carry every trade incl. candidate
// snapshots — far beyond the old 1mb API-payload ceiling
app.use(express.json({ limit: "10mb" }));

// Proves the whole pipeline: Express up + Python math engine reachable.
app.get("/health", async (_req, res) => {
  try {
    const sample = await callEngine("bs_call_price", {
      S: 100, K: 100, T: 1, r: 0.05, sigma: 0.2,
    });
    res.json({ status: "ok", mathEngine: "ok", sampleBsCall: sample });
  } catch (err) {
    res.status(500).json({
      status: "degraded",
      mathEngine: "unreachable",
      error: String(err.message || err),
    });
  }
});

// v1.6.0 local accounts: auth is open; per-account storage routes require an
// active account (session.js). Pure-compute + shared-cache routes (detect,
// calculate, recommend, data, screener/ICS cache, market pulse) stay open —
// they hold no per-user data.
app.use("/auth", authRouter);

app.use("/detect", detectRouter);
app.use("/calculate", calculateRouter);
app.use("/recommend", recommendRouter);
app.use("/data", dataRouter);
app.use("/journal", requireAccount, journalRouter);
app.use("/trades", requireAccount, journalRouter); // pre-v1.1 alias
app.use("/paper", requireAccount, paperRouter);
app.use("/etf-screener", requireAccount, etfRouter);
app.use("/account", requireAccount, accountRouter); // v1.7.2 backup/restore
app.use("/screener", screenerRouter);
app.use("/market", marketRouter);
app.use("/archive", archiveRouter); // Phase 0: daily chain snapshots

// JSON everywhere: unknown routes and unhandled errors never leak HTML.
app.use((req, res) => {
  res.status(404).json({ error: `no route ${req.method} ${req.path}` });
});
// eslint-disable-next-line no-unused-vars -- express identifies error
// middleware by arity, the 4th parameter must exist
app.use((err, _req, res, _next) => {
  console.error("[server] unhandled:", err);
  res.status(500).json({ error: "internal error; see server logs" });
});

const PORT = process.env.PORT || 3001;
const HOST = process.env.OD_HOST || "127.0.0.1"; // loopback only; never 0.0.0.0 by default
if (require.main === module) {
  const server = app.listen(PORT, HOST, () => {
    console.log(`option-obelisk backend listening on http://${HOST}:${PORT}`);
  });
  archiveService.start(); // snapshots the watchlist after the close when missing
  const shutdown = () => {
    archiveService.stop();
    engineClient.shutdown(); // stop the warm Python child
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 2_000).unref();
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

module.exports = app;
module.exports.ALLOWED_ORIGINS = ALLOWED_ORIGINS;
