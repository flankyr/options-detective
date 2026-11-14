# Pricing core v2 (Phase 2)

Modules: `backend/math/bsm.py`, `american.py`, `smile.py`, `volmodel.py`.
v1's `black_scholes.py`, `iv_solver.py` and `probability.py` are unchanged and
still back the 1,300-test suite; v2 adds alongside them and the Node services
now call the v2 entry points.

## What changed and why

| v1 | v2 | Why it matters |
|---|---|---|
| European Black-Scholes, no dividends | Black-Scholes-Merton with continuous yield `q`, rate `r` per expiry from the Treasury curve | A 2% yield moves a one-year ATM call by ~1 point; the hard-coded 4% rate was off by up to 40 bp across the curve |
| European only | CRR binomial (reference) + Barone-Adesi-Whaley (fast) for American exercise; `exercise_style()` marks SPX/XSP/NDX/RUT/VIX European | US single-name and ETF options are American. Short puts on dividend payers and deep ITM positions carry an early-exercise premium v1 could not see |
| Vendor IV from yfinance, our solver unused | Every contract's IV solved from mid (BSM, vectorised); vendor IV kept as `ivVendor`; `vendorDeviation` flag at >15% | Thin strikes often carry junk vendor IV; now the number is ours and checkable |
| One ATM sigma for all strikes | SVI smile per expiry (Durrleman butterfly check), PCHIP spline fallback under 6 strikes; total-variance interpolation in time | Skew is real; a put spread's odds depend on the put wing's vol, not the ATM |
| Greeks at leg IV, probabilities at ATM IV | Greeks AND the screen's sigma both come from the fitted smile (`ivSmile`, `smiles[exp].atmIv`) | One card, one model |

## Formulas

Black-Scholes-Merton, continuous yield `q`:

```latex
d_1 = \frac{\ln(S/K) + (r - q + \tfrac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}, \qquad d_2 = d_1 - \sigma\sqrt{T}
```
```latex
C = S e^{-qT} N(d_1) - K e^{-rT} N(d_2), \qquad P = K e^{-rT} N(-d_2) - S e^{-qT} N(-d_1)
```

Raw SVI on total variance `w = \sigma^2 T`, `k = \ln(K/F)`, `F = S e^{(r-q)T}`:

```latex
w(k) = a + b\left(\rho (k - m) + \sqrt{(k-m)^2 + s^2}\right)
```

Constraints `b \ge 0`, `|\rho| < 1`, `s > 0`, `a + b s \sqrt{1-\rho^2} \ge 0`. Butterfly
arbitrage is absent when Durrleman's `g(k) \ge 0`:

```latex
g(k) = \left(1 - \frac{k w'}{2w}\right)^2 - \frac{w'^2}{4}\left(\frac{1}{w} + \frac{1}{4}\right) + \frac{w''}{2}
```

## Numerics

- IV: Newton from the Brenner-Subrahmanyam seed, bracket maintained, 60-step
  bisection polish for stragglers. Full SPY chain (~2,000 contracts) solved in
  ~20 ms; BAW over 2,000 contracts ~80 ms; CRR 300 steps ~5 ms per contract.
- BAW vs CRR(400): within $0.01 near the money, up to ~$0.09 deep ITM puts
  (known BAW bias). The analyzer uses CRR for a single position; the screener
  uses BAW.
- SVI fit: `scipy.optimize.least_squares` with bounds and a soft `w >= 0`
  penalty; rejected when RMSE > 5 vol-points (falls to spline).

## Limitations (tracked)

- Continuous yield stands in for discrete dividends. Ex-dates inside an
  option's life are approximated; escrowed-dividend handling is a Phase 6 item.
- IV convention is European BSM even for American underlyings (market
  convention). The American premium lives in `american.py`, not in the vol.
- The screen still uses the smile's ATM vol as the single sigma for the
  lognormal POP. Phase 3 replaces that with the full market-implied density.
