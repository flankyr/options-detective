"""yfinance: unofficial Yahoo endpoints. Live snapshot only, no SLA.

Kept as the last-resort live source and labelled as such in the UI. All the
normalization logic lives in market_data.py (it predates this package); this
module adapts its output to schema v2 and adds dividends.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from .base import Provider, Unsupported
from .schema import Capabilities, clean, finalize


class YFinanceProvider(Provider):
    name = "yfinance"
    capabilities = Capabilities(
        live=True, historical=False, delayed_minutes=15, has_greeks=False,
        has_open_interest=True, has_volume=True, history_depth_days=0,
        license_note="Unofficial Yahoo Finance endpoints; personal use, no redistribution.",
    )

    def __init__(self, fetch=None):
        self._fetch = fetch  # injectable for tests

    def _market_data(self):
        if self._fetch is None:
            from market_data import fetch  # lazy: network module
            self._fetch = fetch
        return self._fetch

    def chain(self, symbol, asof=None, *, max_expirations=6, min_dte=1, max_dte=120):
        if asof is not None and asof != date.today():
            raise Unsupported("yfinance has no historical option chains")
        payload = self._market_data()(symbol, max_expirations, min_dte, max_dte)
        for chain in payload["chains"].values():
            for side in ("calls", "puts"):
                for c in chain[side]:
                    c["ivVendor"] = c.get("impliedVolatility")
        today = datetime.now(timezone.utc).date().isoformat()
        return finalize(payload, provider=self.name, asof=today,
                        capabilities=self.capabilities,
                        dividend_yield=self.dividend_yield(symbol, payload["price"]))

    def underlying_history(self, symbol, start, end):
        import yfinance as yf
        hist = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(),
                                         auto_adjust=True)
        return [(idx.date().isoformat(), float(row["Close"])) for idx, row in hist.iterrows()]

    def dividend_yield(self, symbol, price):
        try:
            import yfinance as yf
            divs = yf.Ticker(symbol).dividends
            if divs is None or len(divs) == 0:
                return 0.0
            cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=365)
            trailing = divs[divs.index.tz_convert("UTC") >= cutoff] if divs.index.tz is not None \
                else divs[divs.index >= cutoff.replace(tzinfo=None)]
            total = clean(trailing.sum())
            return round(total / price, 5) if total and price else 0.0
        except Exception:
            return None
