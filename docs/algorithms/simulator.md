# Simulator (Phase 5)

Package: `backend/math/sim/`. Entry: `od-math simsvc`. Routes: `/research/*`.
UI: the Research tab. Closes A14 (no backtesting, no calibration).

## What it does

Replays archived option chains day by day. On date *t* the strategy sees the
chain and the price history as of *t* only (the data service's `asof` path,
`DataService.forecast(..., asof=t)` truncates history). Open positions are
marked at that day's mids (settled at intrinsic once expired; marked at
Black-Scholes on the smile when a contract has dropped out of the window).
Management rules run before new entries so a freed slot can be refilled the
same day. Transaction costs come from the Phase 4 cost model at entry and at
any market exit. The last day liquidates everything so curves are comparable.

## Strategy plug-ins

```python
class MyStrategy(Strategy):
    name = "my_strategy"
    params = {"delta": 0.16, "profitTarget": 0.5, "stopMultiple": 2.0, "exitDte": 7}
    def entries(self, ctx): ...      # candidates in the optimizer's shape
    def manage(self, position, ctx): # "hold" | "close" | "roll" (default rules provided)
```

Drop the file in `sim/strategies/`; it appears in the Research tab. Built in:

| name | rule |
|---|---|
| `optimizer` | the app's screen (`mode`, `view`, `topK`, `maxOpen`, DTE window, allowed structures) with the optimizer's own sizing |
| `strangle_16d` | sell one 16Δ strangle 30–60 DTE whenever flat; 50% target, 2× stop, exit at 7 DTE |
| `put_spread_vrp` | sell a 30Δ put credit spread only when ATM IV / forecast ≥ 1.10 |
| `buy_and_hold` | 100 shares per lot from day one |

## Outputs

- **Equity curve** (daily), **trade log** with entry/exit values, costs, days
  held, MAE/MFE, and the model's predictions at entry (POP, EV, CVaR under the
  ranking distribution and under the market density).
- **Metrics**: total return, CAGR, annualised vol, Sharpe with a 90% block-
  bootstrap interval, Sortino, max drawdown, Calmar, win rate, profit factor,
  average win/loss, tail ratio, expectancy, total costs, turnover.
- **Calibration**: predicted-POP buckets vs realized win rate, Brier score
  (ranking distribution and market density as the control), and a regression
  of realized P&L per unit on predicted EV (slope ≈ 1 means the EV scale is
  believable, ≈ 0 means the edge did not show up).
- **Walk-forward** (`sim/walkforward.py`): rolling train/test parameter
  selection with a stitched out-of-sample curve; and a forecaster bake-off
  (HAR vs EWMA vs GARCH, rolling origins, log RMSE and bias).
- Every run is saved under `<data dir>/research/` as JSON.

## Honest limits

- Fills at mid less the modelled half-spread; no partial fills, no slippage
  beyond the cost model, no early assignment (positions cash-settle at expiry).
- Cross-expiry structures are marked with sticky-strike vol.
- The forecast is unavailable until 61 archived closes exist, so the first
  three months of any archive run in market/directional mode.
- Speed: ~1 s per screening day plus ~60 ms per marking day. A two-year run
  screening weekly takes about two minutes. The Electron IPC bridge times out
  at five minutes; longer runs should narrow the date range or screen less often.
- The archive is the history. Until it has accumulated (or DoltHub is
  reachable for the symbol), there is nothing to test on. Phase 0 started the
  clock.
