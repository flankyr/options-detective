# Option Obelisk

Desktop options-analysis app for semi-technical traders (formerly Options
Detective). Three integrated layers: **Screener** (candidate detection) →
**Trade Analyzer** (math engine) → **Optimal Strategies** (strategy
advisor), plus a **Position Log**, a risk-free **Sandbox** simulator, and
an **Asset Screener** for ETF discovery.

Stack: Electron + React/Tailwind (frontend), Node.js/Express (backend),
Python + scipy (math engine, called as a subprocess).

## Repository layout

```
electron/    Electron main process + preload IPC bridge
frontend/    React + Vite + Tailwind renderer (3 views)
backend/     Express API (detect / calculate / recommend / data)
backend/math Python math engine: Black-Scholes, greeks, IV, payoff,
             probability, sizing — plus its test suite
docs/        architecture, strategy mapping, API schema
```

## Setup

Prereqs: Node.js >= 18, Python >= 3.11.

```powershell
# 1. Math engine (Python)
cd backend/math
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests        # must be all green

# 2. Backend (Express)
cd ..
npm install
npm run dev                                        # http://127.0.0.1:3001 (loopback only)

# 3. Frontend (React/Vite)
cd ../frontend
npm install
npm run dev                                        # http://localhost:5173

# 4. Electron shell (optional during early phases)
cd ../electron
npm install
npm start
```

Smoke test the full pipeline (Express → Python → back):

```powershell
irm http://localhost:3001/health
# { "status": "ok", "mathEngine": "ok", "sampleBsCall": 10.4506... }
```

## Status and roadmap

v1.10.x shipped the full MVP plus the pre-launch suite: screener, trade
analyzer, recommender, position log, paper-trading sandbox, ETF and
index-component screeners, local accounts, analytics, alerts, templates,
onboarding, five themes, a packaged Windows installer.

**v2 is in progress.** `docs/ROADMAP-v2.md` is the plan of record: a Nov 2026
audit found the math engine sound but the scoring without edge (POP-weighted,
EV ≈ 0 by construction), volatility inputs too crude (single ATM sigma, vendor
IV), sizing broken for income trades, and no backtesting. The roadmap rebuilds
those in order:

| Phase | Release | What |
|---|---|---|
| 0 | v1.11 | hygiene, loopback-only backend, CI, **daily chain archive** |
| 1 | v1.12 | data platform: provider interface, free historical sources |
| 2 | v1.13 | pricing core v2: dividends, American exercise, per-contract IV, smile fit |
| 3 | v1.14 | distribution & EV engine: implied density, realized-vol forecast, VRP |
| 4 | v1.15 | strategy optimizer, size-invariant score, Kelly/stress sizing |
| 5 | v2.0 | simulator: backtester, calibration, strategy plug-ins, Research tab |
| 6 | v2.1 | transparency UI and analyzer mode |
| 7 | v2.2 | signing, auto-update, licensing, public release |

Developer workflow (tests, lint, release steps): `docs/RELEASING.md`.

## Building the installer

```powershell
# 1. Frontend bundle
cd frontend; npm run build

# 2. Math engine as a standalone exe (PyInstaller, no Python needed by users)
cd ../backend; npm run build:math          # -> backend/math/dist/od-math/

# 3. One-click Windows installer (electron-builder, NSIS)
cd ../electron; npm run dist               # -> electron/release/Option-Obelisk-Setup-*.exe
```

The installer is per-user (no admin prompt), installs to
`%LOCALAPPDATA%\Programs\Option Obelisk`, and launches on finish. The
position log lives in the user-data folder, not the install folder. The
binary is unsigned, so SmartScreen will ask for "More info → Run anyway"
on first launch.

**For users: download `Option-Obelisk-Setup-<version>.exe`, run it, done.**
No Python, no Node, no terminal.

## Running the desktop app

```powershell
cd electron
npm start                        # spawns the backend, loads frontend/dist

# or against the Vite dev server (hot reload):
cd frontend; npm run dev         # terminal 1
cd electron                      # terminal 2
$env:VITE_DEV_SERVER_URL = "http://localhost:5173"; npm start
```

## Ground rules (from the build brief)

- **No AI arithmetic** — every number comes from the deterministic engine.
- **All data timestamped** — stale data is flagged, never hidden.
- **Liquidity gates** — volume < 50, OI < 100 or spread > 5% is filtered.
- **Max loss is sacred** — always displayed alongside max profit.
- **Validation gates** — bad inputs degrade gracefully, never silently.
