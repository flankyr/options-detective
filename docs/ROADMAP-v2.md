# Option Obelisk v2 — Plan of Action

Drafted 2026-11-14 from a full repo audit at v1.10.6 (commit b21663d).
Owner: Usmaan Aslam. Implementer: Claude (direct edits on feature branches, PR to `main`).

## Status (2026-11-15)

All eight phases are implemented on stacked feature branches
(`feature/p0-archive` → … → `feature/p7-productisation`), each with green
test suites (1,397 pytest / 142 node / tsc) in a Linux sandbox. What has NOT
happened yet, and only the owner can do:

| Item | Why it waits |
|---|---|
| Push branches, open PRs, merge to `main` | no GitHub credentials in the build session |
| `npm install` in backend/ and frontend/, `pip install -r requirements-dev.txt` | new dev dependencies and Python packages (pandas, pyarrow) |
| `vite build` and `npm run dist` on Windows | the sandbox cannot run the native rollup binary or PyInstaller |
| First live call to Massive / DoltHub / Treasury CSV | no network from the sandbox; providers were built against recorded fixtures |
| First archive snapshot (Settings → Data → Snapshot now) | starts the history the backtester needs |
| Repository rename to `option-obelisk`, branch cleanup (`docs/branch-cleanup.cmd`) | owner decision, already approved in principle |
| Code signing, `GH_TOKEN` for the first published release | owner accounts |

## 0. Where this plan is going

Goal: turn Option Obelisk from a polished *display* of Black-Scholes numbers into a
tool with a defensible edge model, a research-grade simulator for testing new
algorithms, and the engineering hygiene of a commercial fintech product.

Strategic decisions (locked 2026-11-14):

| Decision | Choice |
|---|---|
| End state | Commercial-quality product. Near-term focus: portfolio piece + free public release. Prefer the slower, higher-quality route when it conflicts with speed. |
| Data | Best free sources now, behind a provider interface so a paid feed drops in later. |
| Edge model | Both: (a) volatility risk premium (realized-vol forecast vs implied), (b) user directional view as an overlay. Plus a **Transparent Analyzer** toggle that hides recommendations and shows honest EV≈0 math. |
| Stack | Keep Electron + React/Vite/Zustand + Express + Python engine. No rewrite. |
| Workflow | Claude edits the repo directly on `feature/*` branches, runs both test suites, opens PRs. Usmaan reviews and merges. |

Non-negotiables carried forward from v1 (they are good): every number from the
deterministic engine, all data timestamped, stale flagged never hidden, max loss
always shown, bad inputs degrade gracefully.

New non-negotiables for v2:

- **No claim without calibration.** Any probability or EV the UI shows must have a
  calibration page proving (on historical data) that it means what it says.
- **Every score component is size-invariant.** Doubling the contract count must not
  change a candidate's rank.
- **Costs are in the number.** EV and POP are quoted *after* paying half the bid-ask
  spread on every leg and the user's commission.
- **Model and method are inspectable.** Every candidate links to the inputs that
  produced it (vol used per strike, forecast, drift, costs).

---

## 1. Audit findings (what is wrong today)

Severity: **S1** wrong/unsafe, **S2** misleading or missing core capability,
**S3** quality/polish. IDs are referenced by the phases below.

### Algorithms

| ID | Sev | Finding | Where |
|---|---|---|---|
| A1 | S1 | Composite score has no edge: POP and prices both come from the same implied distribution, so a mid-filled trade has EV≈0 by construction. Weighting POP at 0.30 systematically rewards far-OTM premium selling (many small wins, rare large losses). | `detector.js scoreCandidates`, `scoring.ts` |
| A2 | S1 | Income trades size to **0 contracts** under defaults ($25k, 2%): covered call / CSP max loss = stock to zero; strangle max loss = undefined → `contracts = 0`. | `detector.js`, `calculator.js`, `sizing.py` |
| A3 | S2 | Single ATM sigma for all strikes in POP/PMP: ignores skew. Put spreads on equities are mispriced vs market-implied odds. | `detector.js` ctx.atmIv → `probability.py` |
| A4 | S2 | Greeks use per-leg IV, probabilities use ATM IV → internally inconsistent numbers on the same card. | `detector.js`, `calculator.js` |
| A5 | S2 | IV is taken from yfinance's `impliedVolatility` field; the in-house `iv_solver.py` is never called in production. Vendor IV is unreliable on thin strikes. | `market_data.py normalize_contracts` |
| A6 | S2 | IV rank compares today's ATM *implied* vol with a year of *realized* vol. Not like-for-like; IV sits above RV most of the time so rank reads high. | `market_data.py iv_rank` |
| A7 | S2 | European BS, no dividends, r hard-coded 4%. US single names are American; dividends drive early-assignment risk on short calls. | `black_scholes.py`, `DEFAULTS.riskFreeRate` |
| A8 | S2 | Strike selection by fixed %OTM (4%/8%) and 2 variants per strategy. Industry practice is delta-based. Condor wings fixed at one strike. | `candidates.js` |
| A9 | S2 | 7 strategies only. Long call/put, calendars, diagonals, butterflies, ratio spreads listed in `strategy-mapping.md` but not built. | `strategyMapping.js`, `candidates.js` |
| A10 | S2 | Mapping inconsistencies: bearish+high-IV → iron condor / short strangle (neutral structures). | `strategyMapping.js TABLE` |
| A11 | S2 | Theta component is normalized only against the current screen's candidates and is not per-dollar-of-capital → size-dependent and screen-dependent. | `detector.js scoreCandidates` |
| A12 | S2 | ror component: unbounded upside = 1.0, unbounded risk = 0.15 — arbitrary constants. | `detector.js` |
| A13 | S2 | Capital for undefined-risk = `0.2 × spot × 100`, flagged approximate. Real Reg-T strangle margin is max(20%·spot − OTM + premium, 10%·strike + premium) + other side's premium. | `positionMath.js capitalRequiredOf` |
| A14 | S2 | No backtesting, no calibration, no out-of-sample evidence for any score. | — |
| A15 | S3 | Liquidity component uses min leg volume and mean spread only; ignores OI depth and size relative to volume. | `detector.js liquidityOf` |
| A16 | S3 | DTE uses 21:00 UTC close; ignores exchange holidays and 16:15 ET close on index products. | `detector.js dteOf` |

### Data

| ID | Sev | Finding |
|---|---|---|
| D1 | S1 | `backend/math/requirements.txt` omits `yfinance`; a fresh install cannot fetch data. |
| D2 | S1 | yfinance is an unofficial scraper of Yahoo endpoints: no SLA, breaks periodically, and Yahoo's terms are a distribution risk for a public product. |
| D3 | S2 | No historical option chains anywhere → nothing to backtest on. |
| D4 | S2 | Expirations capped at 6 and chains fetched whole; no incremental refresh. |
| D5 | S3 | `fx.js` / `etfUniverse.js` have their own fetch paths; three different ways to call Python. |

### Security / infrastructure

| ID | Sev | Finding |
|---|---|---|
| I1 | S1 | `app.listen(PORT)` binds all interfaces and `cors()` is wildcard → any LAN device or any website can hit `/detect`, `/calculate`, and (with a token) account routes. Must bind `127.0.0.1` and restrict origin to the Electron renderer / Vite dev origin. |
| I2 | S2 | Installer is unsigned (SmartScreen warning). No auto-update. |
| I3 | S2 | Local account data unencrypted at rest (documented, acceptable for now; revisit before commercial). |
| I4 | S3 | `dev-isolated.js` + `.env.isolated` dual-port setup is undocumented in README. |

### Code quality

| ID | Sev | Finding |
|---|---|---|
| C1 | S3 | `SettingsTabs.tsx` 1,143 lines, `store.ts` 944, `EtfScreener.tsx` 801, `Journal.tsx` 733. |
| C2 | S3 | ~30 stale branches; both `main` and `master` exist. |
| C3 | S3 | `LaunchCodes.txt` references `Options-Detective-Setup-0.1.0.exe`; package names still `options-detective-*`. |
| C4 | S3 | No CI. Tests only run when someone remembers. |
| C5 | S3 | No lint/format config (ESLint, Prettier, ruff). |
| C6 | S3 | Frontend has zero tests; Node tests silently fail (21/126) without a venv — should skip with a clear message or be gated. |
| C7 | S3 | Docs drift: `strategy-mapping.md` lists strategies that don't exist; README build-status list stops at v1.x. |

---

## 2. Target architecture

```
                ┌──────────────────────────────────────────────────────┐
                │  Data platform (Python)                              │
                │  providers/  yfinance | massive | tradier | dolthub  │
                │  → normalized Chain schema (parquet archive, SQLite  │
                │    index) → point-in-time reads for backtests        │
                └───────────────┬──────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  Pricing core v2 (Python, numpy-vectorised)                          │
 │  black_scholes (div yield, term r) · american (CRR/BAW) · iv_solver  │
 │  per contract · smile fit per expiry (SVI / arb-free spline)         │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  Distribution & EV engine                                            │
 │  market-implied density from smile (Breeden-Litzenberger)            │
 │  + realized-vol forecast (HAR-RV default, EWMA, GARCH)               │
 │  + user view (drift / vol tilt)  → P(S_T), EV, POP, CVaR, EV/CVaR    │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  Strategy optimizer                                                  │
 │  delta-grid strike search · 14 structures · cost model               │
 │  · size-invariant score (EV per unit CVaR, after costs)              │
 │  · sizing: fractional Kelly on model P&L, capped by stress loss      │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
 ┌─────────────────────────────┐   ┌────────────────────────────────────┐
 │ Simulator                   │   │ App (Express + React)              │
 │ backtest (point-in-time)    │   │ Screener · Analyzer · Recommender  │
 │ calibration · walk-forward  │   │ Transparent-mode toggle · Journal  │
 │ paper sandbox (existing)    │   │ Sandbox · Research tab (new)       │
 └─────────────────────────────┘   └────────────────────────────────────┘
```

Key design rules:

- **One Python process, JSON-lines protocol** (already exists). All new engine
  functions register in `engine.py DISPATCH`. Data fetching moves into the same
  process behind `data.*` functions so there is one bridge, not three.
- **Point-in-time everywhere.** The chain schema carries `asof` and `fetchedAt`.
  Backtests only read `asof ≤ t`. Live screening is the special case `t = now`.
- **Model objects are explicit.** A `VolModel` (smile per expiry), a
  `Distribution` (density on a strike grid), and a `CostModel` are first-class
  JSON payloads the UI can show. No hidden sigma.

---

## 3. Phases

Estimates are effort-weeks at roughly 10–15 focused hours/week alongside school.
Each phase ends with a tagged release and green suites. Phases 2–4 are the
"tuned algorithm"; Phase 5 is the "simulator".

### Phase 0 — Hygiene, safety, baseline (1 week) → v1.11.0

Fixes: D1, I1, C2, C3, C4, C5, C6, C7.

- Add `yfinance` to `requirements.txt`; pin versions; add `requirements-dev.txt`.
- `app.listen(PORT, "127.0.0.1")`; CORS allowlist = Electron `file://`/`app://` origin + `http://localhost:5173`.
- Rename packages to `option-obelisk-*`; update `LaunchCodes.txt` → `docs/RELEASING.md`.
- Delete merged branches locally and on origin; delete `master`; protect `main`.
- GitHub Actions: `pytest`, `node --test`, `tsc --noEmit`, `vite build` on every PR (Windows runner so the venv path logic is exercised).
- ESLint + Prettier (frontend/backend), ruff + black (Python). Format once in a dedicated commit.
- Node tests: detect missing venv and `skip` the engine-dependent suites with an explicit reason instead of 21 red tests.
- README: replace build-status list with "Architecture / Status / Roadmap" pointing here; document dual-port dev setup.
- **Start the archive now**: a scheduled job (`backend/math/archive.py`, run by Task Scheduler or on app launch once/day) snapshots full chains for a watchlist (SPY, QQQ, IWM, AAPL, MSFT, NVDA, TSLA, AMZN + user's symbols) to `data/archive/{symbol}/{date}.parquet`. Every day not recorded is a day of backtest history lost.

Acceptance: CI green on a clean clone; `curl` from another LAN host to :3001 refused; archive has ≥1 snapshot per watchlist symbol.

### Phase 1 — Data platform (2 weeks) → v1.12.0

Fixes: D2, D3, D4, D5.

- `backend/math/data/` package with a `Provider` protocol:
  `chain(symbol, asof=None)`, `underlying_history(symbol, start, end)`,
  `dividends(symbol)`, `rates()`. Each provider declares `capabilities`
  (live/delayed/eod, history depth, has_greeks, has_oi, license_for_redistribution).
- Providers, in priority order with automatic fallback:
  1. **Local archive** (parquet) — always first for `asof < today`.
  2. **Massive** (formerly Polygon) free tier — 2 years EOD chains, 5 req/min. Used to backfill the archive for the watchlist over the first weeks (rate-limited background job).
  3. **Tradier sandbox** — 15-min-delayed chains with Greeks; needs an account (verify Canadian-resident eligibility; if blocked, skip).
  4. **DoltHub `post-no-preference/options`** — free SQL endpoint, EOD chains with greeks; good secondary history source; validate against Massive before trusting.
  5. **yfinance** — snapshot of last resort; clearly labelled `provider: yfinance (unofficial)` in the UI.
- Risk-free rate: fetch US Treasury par curve (treasury.gov CSV) daily; interpolate to each expiry. Dividend yield: trailing 12-month from provider, override field in UI.
- Normalized schema v2 (`docs/api-schema.md`): add `asof`, `provider`, `underlyingClose`, `dividendYield`, `rate`, per-contract `iv_vendor` (kept) and `iv_model` (ours, Phase 2).
- Incremental refresh: only re-fetch expirations whose `fetchedAt` is older than TTL.
- Settings → Data: provider status, API keys (stored via Electron `safeStorage`), archive size, "backfill now".

Acceptance: `provider.chain("SPY", asof="2025-06-13")` returns a chain from archive/Massive; live screen works with yfinance disabled; schema tests pass for every provider via recorded fixtures.

### Phase 2 — Pricing core v2 (3 weeks) → v1.13.0

Fixes: A5, A7, part of A4.

- `black_scholes.py`: add continuous dividend yield `q`; `r` and `q` per expiry. Keep the existing API as a thin wrapper so 1,319 tests stay green; add a `v2` namespace.
- `american.py`: Cox-Ross-Rubinstein binomial (vectorised, 200–500 steps) for price + greeks on American-style equity options; Barone-Adesi-Whaley as the fast approximation for screening; index options (SPX, XSP, NDX, RUT) and ETFs flagged European/American correctly via a style table. Early-exercise boundary exposed so the UI can show "assignment likely if S < X before ex-div".
- IV solver runs on **every** contract from mid (fallback last) → `iv_model`. Flag contracts where `|iv_model − iv_vendor| > 15%` relative (vendor junk).
- `smile.py`: per-expiry fit of `iv_model` vs log-moneyness. Default **SVI** (Gatheral) with Durrleman arbitrage checks; fallback cubic spline with linear wings when < 6 valid strikes. Output: fitted params, RMSE, strikes used/rejected. Term structure: linear-in-variance interpolation between expiries.
- Greeks per leg computed off the **smile** vol at that strike, and the same smile feeds Phase 3 probabilities → consistency (A4).
- Numerics: move hot paths to numpy arrays (price whole chains at once). Benchmark target: full SPY chain (≈2,000 contracts) IV-solved + smile-fit in < 300 ms.

Acceptance: American put on dividend payer prices above European; put-call parity holds on fitted smiles within 1 tick; smile RMSE < 0.5 vol-pt on liquid names; greeks on a card are internally consistent with its POP.

### Phase 3 — Volatility forecast & distribution engine (3 weeks) → v1.14.0

Fixes: A1 (model side), A3, A6.

- `distribution.py`: market-implied terminal density from the smile via Breeden-Litzenberger (second derivative of call price in strike), normalised and tail-extrapolated. This is the "what the market thinks" distribution.
- `volforecast.py`: realized-vol forecasters on daily (and, when available, intraday) closes:
  - **HAR-RV** (Corsi) — default; robust, few parameters.
  - EWMA (RiskMetrics λ=0.94) — baseline.
  - GARCH(1,1) via `arch` — optional, slower.
  Each outputs σ̂ over horizon `T` with a confidence band from historical forecast errors.
- **VRP signal**: `vrp = iv_atm(T) − σ̂(T)`; standardised against that symbol's own VRP history (z-score). Replaces A6's IV rank. "IV rank" stays available but is computed correctly from the archived ATM-IV series once ≥ 60 days exist, and labelled "proxy" until then.
- **Scenario distribution** (what *we* think): lognormal-mixture or shifted market density with
  - vol replaced by σ̂ (VRP mode),
  - drift tilt from the user's view (Directional mode: bullish/bearish → ±k·σ̂ drift, user slider for conviction),
  - both.
- Metrics on any legs under any distribution: `EV`, `POP`, `P(max profit)`, `CVaR_5%`, `expected shortfall at expiry`, `EV / CVaR` (the ranking statistic), `kelly_fraction`.
- **Transparent Analyzer mode**: distribution = market-implied, costs on. By construction EV ≤ 0 by about the half-spread; the UI says so explicitly. No recommendation language anywhere in this mode.

Acceptance: BL density integrates to 1 and reprices the input calls within 1 tick; HAR-RV beats EWMA out-of-sample on SPY 2024–2026 by RMSE; under market density with zero costs, every structure's EV is within ±$0.01 of 0 (unit test — this is the core sanity check that A1 is understood).

### Phase 4 — Strategy optimizer, scoring, sizing (3–4 weeks) → v1.15.0

Fixes: A1 (scoring side), A2, A8–A13, A15, A16.

- `structures.py`: 14 structures with delta-grid search instead of %OTM:
  long call/put, covered call, CSP, call/put verticals (debit & credit), iron condor, iron butterfly, long/short straddle, long/short strangle, calendar, diagonal, ratio spread (flagged undefined risk). Search short strikes over {10,16,20,25,30,40}Δ and widths over {1,2,3,5} strike steps; dedupe on legs.
- Strategy mapping rewritten as **scores, not tables**: each structure has an exposure signature (Δ, Γ, vega, θ sign). Eligibility = sign agreement with (VRP z, user view). Fixes A10.
- **Cost model**: half-spread per leg (configurable 0–100% of spread), commission/contract, assignment fee; slippage widened for `indicative` marks.
- **Score v2** (0–10), all components size-invariant and screen-invariant:
  - EV / CVaR after costs (primary, 0.40)
  - POP under scenario distribution (0.15)
  - θ per $ of capital at risk (0.15)
  - Liquidity: spread/mid, OI/size needed, volume (0.15)
  - Model confidence: smile RMSE, forecast band width, data age (0.15)
  User weights still re-mix these on the frontend (keep `scoring.ts` pattern).
- **Sizing v2**: contracts = min(fractional-Kelly (¼ Kelly default) on model P&L distribution, stress-loss cap (user's % of account at a −2σ / +2σ move, not stock-to-zero), buying-power cap using real Reg-T formulas (A13)). Income trades now size > 0 with an explicit "assignment would use $X buying power" line.
- Calendar: NYSE holiday table + per-product close times for DTE (A16).
- Rationale text generated from the actual drivers ("ranks 1st because EV $38 on $410 CVaR after $6 costs; IV 4.1 pts above HAR forecast").

Acceptance: ranking is unchanged when `qty` is doubled; a known-rich-IV fixture ranks short premium above long; a known-cheap-IV fixture flips; income trades size > 0 under defaults; every candidate carries `model` block (smile params, σ̂, drift, costs).

### Phase 5 — Simulator (4 weeks) → v2.0.0

Fixes: A14. This is the research environment.

- `backend/math/sim/`:
  - **Backtester**: for each date t in the archive, run the live screening pipeline with `asof=t` (no look-ahead: Phase 1 guarantees it), open top-K by score with Phase 4 sizing, mark daily with the archive's mids, close at expiry/stop/target per rule set. Outputs trade list + equity curve + per-trade MAE/MFE.
  - **Calibration**: bucket predicted POP into deciles vs realised hit rate; same for EV vs realised P&L. Brier score, reliability diagram. Also for the market-implied distribution (should be calibrated by definition — a useful control).
  - **Walk-forward**: forecaster and score weights fitted on window `[t−N, t)`, evaluated on `[t, t+M)`; rolling.
  - **Strategy plug-in API**: a strategy is a Python class with `screen(ctx) → candidates`, `manage(position, ctx) → action`. The built-in optimizer is one plug-in; users (and we) can test new algorithms by dropping a file in `sim/strategies/`.
  - Metrics: CAGR, Sharpe/Sortino on daily equity, max DD, Calmar, win rate, profit factor, tail ratio, turnover, exposure.
- Paper sandbox (existing `paperTrading.js`): switch marks and settlement to the v2 engine; add "replay" mode that plays an archived date range at N× speed so the UI can be exercised against history.
- **Research tab** in the app: pick strategy plug-in, universe, dates, cost model → run → equity curve, calibration plot, trade table, export CSV. Runs in the Python process with progress streamed over the JSON-lines bridge.
- Reference strategies shipped as plug-ins for comparison: "sell 16Δ strangle monthly", "sell 30Δ put spread when VRP z > 1", "buy-and-hold underlying".

Acceptance: a backtest over 2 years of archived SPY completes in < 2 min; calibration plot for the market-implied POP lies on the diagonal within noise; the optimizer's Sharpe is reported with a bootstrap CI and compared to the reference strategies in the UI (whatever the result — the point is honest measurement).

### Phase 6 — Transparency UI & analyzer (2–3 weeks) → v2.1.0

- Mode switch in header: **Analyzer** (transparent, market-implied, no recommendations) / **VRP** / **Directional** / **Both**.
- Candidate card: "Why this number" drawer → smile plot with the legs' strikes, forecast vs implied bar, density with P&L overlaid, cost breakdown.
- Vol surface view per symbol (smile per expiry + term structure); VRP history chart.
- Position management rules (take-profit at 50% credit, stop at 2× credit, 21-DTE roll) as explicit, testable settings used by both the sandbox and backtester.
- Glossary/Help updated for every new concept (VRP, HAR, CVaR, Kelly, SVI, early exercise).
- Split C1 files along feature lines while touching them.

### Phase 7 — Productisation (2 weeks) → v2.2.0 public release

Fixes: I2, I3 (partial), D2 licensing.

- Code signing: OV certificate (~US$200–400/yr) or Azure Trusted Signing (~US$10/mo) — decide at Phase 6. Enable `electron-updater` with GitHub Releases.
- Data licensing review: display Massive/Tradier attribution where required; yfinance only as user-opt-in "unofficial source" with disclaimer; no redistribution of raw vendor data.
- Disclaimers: not investment advice; Canadian/US regulatory wording reviewed before anything paid.
- Encrypt account store with Electron `safeStorage` key (I3).
- Crash reporting (Sentry self-hosted or GlitchTip) opt-in; anonymous usage metrics opt-in.
- Docs site: methodology white paper (one page per model), API schema, release notes, contributor guide.

### After v2.2 (commercial track, not scheduled)

Paid data feed integration (Massive Advanced / ORATS), broker order routing via Tradier/IBKR, multi-user sync, Mac build, subscription licensing.

---

## 4. Engineering practices (from Phase 0 onward)

- Branch `feature/<phase>-<topic>`; PR to `main`; squash-merge; tag `vX.Y.Z` on merge of the phase's last PR; `docs/releases/vX.Y.Z.md` per release.
- Tests: pytest for every engine function (property tests with `hypothesis` for parity/monotonicity), Node tests for services with recorded fixtures, Vitest + React Testing Library for new UI, one Playwright smoke test for the Electron shell.
- Fixtures: recorded real chains (SPY, AAPL, a thin small-cap) in `backend/math/tests/fixtures/` so no test needs the network.
- Performance budget: screen of one symbol < 1.5 s warm; backtest 500 trading days < 2 min.
- Every PR touching math updates `docs/algorithms/` (method, formula, references, limitations).

## 5. Risks and open questions

| Risk | Mitigation |
|---|---|
| Free providers change terms or disappear (yfinance especially) | Provider interface + archive; never depend on one source; archive is ours. |
| Massive free tier too rate-limited to backfill watchlist | Backfill is a background job over weeks; prioritise SPY/QQQ; DoltHub as parallel source. |
| Tradier requires US residency | Treat as optional provider; don't build anything that only works with it. |
| Smile fits fail on illiquid names | Spline fallback + "model confidence" score component makes low-quality fits visible rather than hidden. |
| Backtest shows the optimizer has no edge | That is a valid result and the Transparent Analyzer mode is the product in that case. Publish the calibration either way. |
| Scope creep from 14 structures × modes × UI | Each phase ships independently; structures beyond the core 8 can slip to 2.1 without blocking the simulator. |
| School load | Phases are 2–4 weeks each with a shippable end; pausing between phases loses nothing. |

Open questions to settle during Phase 0/1 (not blocking):

1. Tradier account eligibility for a Canadian resident.
2. Watchlist for the daily archive (default proposed above).
3. Whether to drop the ETF/Index-component screeners from the main nav into a "Discovery" sub-tab to make room for Research.
4. Kelly fraction default (¼ proposed) and stress move (±2σ proposed).

## 6. Immediate next steps (first two weeks)

1. Branch `feature/p0-hygiene`: requirements fix, 127.0.0.1 + CORS, renames, lint/format, CI, test skip logic. PR.
2. Branch `feature/p0-archive`: `archive.py` + daily snapshot job + Settings toggle. PR. **Start archiving the day it merges.**
3. Branch cleanup on origin (list for approval before deleting).
4. Register Massive free API key; smoke-test 2-year SPY EOD chain pull; measure rate limits.
5. Write `docs/algorithms/01-why-pop-is-not-edge.md` — the one-page explanation that anchors the whole v2 redesign and later becomes the first page of the methodology docs.
