# Option Obelisk — Methodology

One page on how the app arrives at every number it shows. Each section links
to the detailed note in `docs/algorithms/`.

## 1. Data

Live option chains come from the first configured source (Massive, 15-minute
delayed, with greeks and open interest; otherwise Yahoo Finance through
yfinance). Historical chains come from the app's own daily archive, taken
after each close, and from the DoltHub `post-no-preference/options` dataset.
Risk-free rates are read off the US Treasury daily par curve and interpolated
to each expiry; dividend yields are trailing twelve months. Every payload
names its source and the date it describes, and every screen shows them.
([data platform](api-schema.md))

## 2. Pricing

Options are priced with Black-Scholes-Merton (dividend yield, per-expiry rate).
American-style contracts (US stocks and ETFs) are valued with a binomial tree
in the Analyzer and the Barone-Adesi-Whaley approximation in the screener;
index options are European. Implied volatility is solved by the app for every
contract from its mid price; the vendor's figure is kept beside it and flagged
when the two disagree by more than 15%. ([pricing core](algorithms/pricing-core-v2.md))

## 3. Volatility smile

One smooth curve of implied volatility per expiry is fitted to the solved
vols (Gatheral's SVI with a butterfly-arbitrage check; a shape-preserving
spline on thin chains). Greeks, probabilities and the implied distribution
all read volatility from that curve, so the numbers on a card agree with one
another. Beyond the quoted strikes the curve is held nearly flat.
([pricing core](algorithms/pricing-core-v2.md))

## 4. Distributions and expected value

The market-implied distribution of the underlying at expiry is the second
strike-derivative of the fitted call-price curve (Breeden-Litzenberger).
Under it every fairly priced structure has expected value of zero before
costs; the app's "Analyzer" mode shows exactly that. Edge, if any, comes from
a different distribution: the market density rescaled to a realized-vol
forecast (HAR-RV by default), tilted by the user's directional view, or
both. Under the chosen distribution the app reports EV after financing and
transaction costs, probability of profit, probability of maximum profit, the
average loss in the worst 5% of outcomes (CVaR), EV per dollar of CVaR, the
expected move and a Kelly fraction.
([why POP is not edge](algorithms/01-why-pop-is-not-edge.md),
[distribution engine](algorithms/distribution-engine.md))

## 5. Strategy search and score

Seventeen structures are built by delta (short strikes at 10–40Δ, wings one
to five strikes wide) and filtered by exposure signature against the regime:
short-vega structures when implied exceeds forecast volatility, long-vega
when it falls short, delta aligned with the stated view. Each candidate is
scored 0–10: EV per CVaR 40%, POP 15%, theta per dollar of capital 15%,
liquidity 15%, model confidence 15%. Every component is per-lot, so position
size never changes the ranking. Costs are half the bid-ask spread per leg
each way, commissions and expected assignment fees.
([optimizer](algorithms/optimizer-v2.md))

## 6. Sizing

Contracts are the smallest of three caps: the risk budget divided by the loss
at a two-sigma move (not "stock to zero"), the buying-power cap divided by
the capital the position ties up (cash-secured, defined-risk or Reg-T
estimate), and a quarter of the Kelly fraction under the ranking
distribution. The binding cap is named on the card.

## 7. Validation

The Research tab replays archived chains day by day with no look-ahead,
charges the same cost model, and reports return, drawdown, Sharpe with a
bootstrap confidence interval, win rate and profit factor. It also calibrates
the model's own predictions: predicted probability of profit against realized
win rate (Brier score) and predicted against realized expected value. The
screener's score is a hypothesis until this page agrees with it.
([simulator](algorithms/simulator.md))

## 8. What the app does not do

It does not forecast direction (the user supplies the view), does not model
discrete dividends or early assignment paths (continuous yield and cash
settlement), does not account for margin calls or partial fills, and does not
know anything the delayed data does not contain. The glossary inside the
app repeats these limits where they matter.
