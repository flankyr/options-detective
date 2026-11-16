// Chain archive (Phase 0):
//   GET  /archive/status          -> settings, watchlist, per-symbol day counts
//   POST /archive/snapshot        -> { force?, symbols? } run now
//   PUT  /archive/settings        -> { enabled?, extraSymbols?, maxExpirations? }
const express = require("express");

const { archiveService } = require("../services/archive");

const router = express.Router();

router.get("/status", async (_req, res) => {
  try {
    res.json(await archiveService.status());
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

router.post("/snapshot", async (req, res) => {
  try {
    const { force, symbols } = req.body || {};
    res.json(await archiveService.snapshot({
      force: Boolean(force),
      symbols: Array.isArray(symbols) ? symbols : undefined,
    }));
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

router.put("/settings", (req, res) => {
  const body = req.body || {};
  const patch = {};
  if (typeof body.enabled === "boolean") patch.enabled = body.enabled;
  if (Array.isArray(body.extraSymbols)) patch.extraSymbols = body.extraSymbols;
  if (Number.isInteger(body.maxExpirations) && body.maxExpirations >= 1 && body.maxExpirations <= 30) {
    patch.maxExpirations = body.maxExpirations;
  }
  res.json(archiveService.saveSettings(patch));
});

module.exports = router;
