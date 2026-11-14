# Strategy optimizer and score v2 (Phase 4)

Modules: `backend/math/optimizer.py`, `structures.py`, `costs.py`, `mcal.py`.
Node `services/detector.js` resolves the chain and the forecast and makes one
`screen_v2` engine call. Closes A1 (score side), A2, A8–A13, A15, A16.

## What replaced what

| v1 | v2 |
|---|---|
| 7 structures, strikes at fixed 4%/8% OTM, two variants | 17 structures, short strikes searched over {10, 16, 20, 25, 30, 40}Δ, widths over {1, 2, 3, 5} strike steps, deduplicated |
| Eligibility table by (view, IV-rank band) | Exposure signature per structure (sign of Δ, vega, θ) matched to the VRP sign and the user's view; "income" requires θ > 0; a conflicting regime screens everything |
| POP 0.30 · risk/reward 0.20 · theta 0.20 · cap. efficiency 0.15 · liquidity 0.15 | EV/CVaR 0.40 · POP 0.15 · θ per $ at risk 0.15 · liquidity 0.15 · model confidence 0.15 |
| Theta normalised against the screen's other candidates | Annualised θ yield on capital at risk, [-1, 1] → [0, 1]: same number whatever else is on the screen |
| Sizing: risk budget ÷ max loss (stock-to-zero for CC/CSP → 0 contracts) | min(risk budget ÷ 2σ stress loss, buying-power cap ÷ capital, quarter-Kelly); stock-backed income shows one lot with `riskBudgetExceeded` when the budget cannot hold it |
| Capital for undefined risk = 20% × spot × 100 | Reg-T naked margin per short leg: max(20% S − OTM + prem, 10% K + prem), plus long legs' cost |
| No costs | Half-spread per leg (×1.5 on indicative marks), $0.65/contract each way, expected assignment fee |
| 21:00 UTC expiry, no holidays | NYSE holiday table 2024–2028, 16:15 close for index products |

## Score v2

Every component is in [0, 1] and depends on one unit of the position only,
so doubling contracts (or the account) never reorders the list.

| Component | Weight | Definition |
|---|---|---|
| evCvar | 0.40 | EV ÷ |CVaR 5%| under the ranking distribution, after costs; linear map from [−0.5, +0.5] (EV of half the tail loss scores 1) |
| pop | 0.15 | P(P&L > 0) under the ranking distribution |
| theta | 0.15 | net θ × 365 ÷ capital at risk, clipped to [−1, 1], then [0, 1] |
| liquidity | 0.15 | 0.5·(1 − spread%/5%) + 0.25·min(OI/500, 1) + 0.25·min(volume/500, 1); 0.5 flat when the source has no volume/OI |
| confidence | 0.15 | mean of: smile fit (1 − RMSE/5 vol-pt), forecast band (1 − width/2σ), data freshness |

Ranking distribution = scenario density when mode ∈ {vrp, directional, both},
market density in Transparent Analyzer mode (where EV ≤ 0 for everything and
the list is ordered by costs, liquidity and confidence).

## Modes

| mode | distribution | needs |
|---|---|---|
| market | market-implied | nothing |
| vrp | market rescaled to σ_forecast | forecast; falls back to market with a warning |
| directional | market tilted by view × conviction | a view |
| both | both adjustments | forecast + view; degrades to directional or market |

VRP sign: z-score of (IV − forecast) against the symbol's archived history when
≥ 20 days exist (|z| ≥ 0.5), else the raw ratio with a 10% dead band.

## Sizing v2

```
budget      = capital × riskTolerancePct (capped by maxLossDollars)
stress      = worst P&L of one unit at a ±stressSigmas × σ√T move (2σ default)
by_risk     = floor(budget / stress)
by_bp       = floor(capital × maxBuyingPowerPct / capitalRequired)
by_kelly    = floor(kellyFraction × f* × capital / capitalRequired)   (not in market mode)
contracts   = min(by_risk, by_bp, by_kelly)
```

`f*` is the Kelly fraction from the ranking distribution (μ/σ² of return on
capital, clipped to [0, 1]); the default `kellyFraction` 0.25 is quarter-Kelly.
`sizing.binding` names the constraint that bit.

## Cross-expiry structures

Calendars and diagonals are valued at the NEAR expiry: the far leg is marked
at its Black-Scholes value with sticky-strike vol and the remaining time.
Their EV is therefore a model value even in market mode and is labelled
`evaluation.approx`. Breakevens are not reported for them.

## Candidate payload additions

`evaluation {market, scenario, ranking, approx}`, `costs {spread, commission,
assignment, total}`, `metrics {ev, evPct, cvar05, evPerCvar, kellyFraction}`,
`sizing {binding, limits, stressLossPerUnit, riskBudgetExceeded,
capitalBasis}`, `meta {forward, mode, params, exposure, risk, provider, asof}`,
`scoreBreakdown.version = 2`, `probability.popMarket`. The screen envelope
gains `mode`, `vrp`, `forecast`, `scoreVersion`.
