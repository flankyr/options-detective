# Distribution and EV engine (Phase 3)

Modules: `backend/math/distribution.py`, `volforecast.py`; data entry points
in `data/service.py` (`underlying_history`, `forecast`) and `datasvc.py`.

## Densities

| Name | Construction | Use |
|---|---|---|
| market | Breeden-Litzenberger on the Phase 2 smile: `pdf(K) = e^{rT} ∂²C/∂K²` on a 4,001-point log grid widened to cover the wing vols; negative mass clipped and reported; mean checked against the forward | What the market believes. EV ≡ 0 before costs. Transparent Analyzer mode. |
| scenario | Market density with log-returns about the forward rescaled by `σ_forecast / σ_implied` (keeps skew/kurtosis) and shifted by a drift tilt | What we believe. VRP mode = rescale; Directional = tilt; Both. |
| lognormal | Single-sigma Black-Scholes density | v1 baseline, comparisons |

Drift tilt for a view with conviction `c ∈ [0, 1]`: the forward moves by
`c · σ · √T` over the horizon (one full sigma at c = 1).

## Metrics (`position_metrics`)

All on the same grid, one lot: EV (expiry P&L with the entry premium financed
at `r`, minus `costs`), POP, P(max profit), CVaR at 5% (mean P&L of the worst
5% of outcomes), VaR 5%, EV/|CVaR| (the Phase 4 ranking statistic), expected
move, Kelly fraction (`μ/σ²` of return on `capital`, clipped to [0, 1]),
entry cost, financing, costs.

## Forecasters (`volforecast.py`)

| Method | Model | Band |
|---|---|---|
| `har` (default) | Corsi HAR-RV: forward average variance regressed on 1/5/22-day mean squared returns, direct multi-step | ±1 sd of log forecast error over rolling origins |
| `ewma` | RiskMetrics λ = 0.94, flat | same |
| `garch` | GARCH(1,1) MLE with variance targeting, horizon-average variance from the recursion | none (point estimate) |

HAR falls back to EWMA below 73 returns; all need ≥ 60 closes. Realized vol is
close-to-close, 252-day annualised.

## VRP

`vrp = iv_atm − σ_forecast` at the matching horizon. The z-score uses the
symbol's own history of `(archived ATM IV − trailing 21d realized vol)`, which
the Phase 0 archive accumulates; `z` is null until 20 days exist. The ratio
`iv/σ` is also reported.

## Sanity properties pinned by tests

- EV under the market density is within $0.50 of zero for put spreads,
  condors, straddles, strangles and single calls priced on the same smile.
- The market density integrates to 1, has the forward as its mean, reprices
  the input calls within $0.02, and has a fatter left tail than the ATM
  lognormal when the smile has put skew.
- Lognormal mode reproduces v1's `prob_of_profit` and `prob_below`.
- Scaling the forecast vol to half the implied halves the log dispersion;
  bullish tilt raises the mean; short premium gains EV when forecast < implied.
- GARCH recovers simulated (ω, α, β); HAR reacts to a vol regime change.
