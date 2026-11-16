"""Simulator (Phase 5): point-in-time backtesting, calibration, walk-forward.

A Strategy (sim/strategy.py) decides what to open and when to close. The
Backtester (sim/backtest.py) replays archived chains day by day with no
look-ahead: on date t it only sees the chain as of t, marks open positions at
that day's mids, applies the strategy's management rules, and asks the
strategy for new entries. Costs come from the Phase 4 cost model. Predicted
POP/EV at entry are stored with every trade so calibration (sim/calibration.py)
can ask whether a 70% POP really wins ~70% of the time.
"""
