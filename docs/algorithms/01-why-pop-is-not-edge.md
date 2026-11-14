# Why probability of profit is not edge

This page is the reason Option Obelisk v2 exists. It is one paragraph of
mathematics and one worked example, and it is pinned by
`backend/math/tests/test_distribution.py::test_ev_is_zero_under_the_market_density_for_every_structure`.

## The argument

An option's price is the discounted expectation of its payoff under the
market-implied distribution of the underlying at expiry (the risk-neutral
density, which Breeden and Litzenberger showed is the second
strike-derivative of the call price curve). The probability of profit (POP)
that v1 displayed was computed from the *same* implied volatility that set the
option's price. Two quantities derived from one distribution cannot disagree
about expected value:

```latex
\mathbb{E}_Q[\text{payoff}_T] - \text{premium} \cdot e^{rT} = 0
```

for every structure, at every strike, in every expiry. High POP is bought
with a small maximum profit and a large maximum loss, and under the market's
own odds the two always net to zero before transaction costs and slightly
below zero after them.

## Worked example

SPY at 500, 45 days to expiry, 20% ATM vol with a normal put skew (the
synthetic smile in the test). An iron condor selling the 460 put and the 540
call, buying the 450 and 550 wings:

| Quantity | Value |
|---|---|
| POP under the market density | ~75% |
| Max profit (credit) | ~$259 |
| Max loss | ~$741 |
| Expected value, market density, before costs | **$0.00** (test: within $0.50 on a 4,001-point grid) |
| Expected value after half-spreads of $0.03/leg | **−$12** |

Weighting POP at 30% of a composite score, as v1 did, therefore ranked
structures by how far out of the money they sold premium, not by whether they
were good trades.

## Where edge can come from

Only from a distribution that differs from the market's:

1. **Volatility risk premium.** If realized volatility over the option's life
   is systematically below implied volatility (the documented average for
   index options), the scenario density is narrower than the market density
   and short-premium structures have positive EV. The size of that EV depends
   on the forecast error, which is why `volforecast.py` reports a band and
   why Phase 5 calibrates it.
2. **A directional view.** A believed drift shifts the density; positions
   aligned with it gain EV. The app does not supply the view; the user does,
   with a conviction slider that scales a one-sigma move.
3. **Nothing.** The Transparent Analyzer mode uses the market density with
   costs on and says EV ≈ −costs. That is the honest baseline and the mode a
   user should see first.

The score in Phase 4 ranks by EV per unit of tail risk (CVaR) under the
scenario density the user has chosen, after costs. POP is shown, not scored.
