"""Massive (formerly Polygon.io) options REST API.

Free "Options Basic" plan, as documented in Nov 2026: option chain snapshot
with greeks, implied volatility and open interest (quotes are absent on the
starter tiers, so bid/ask may be missing and `mid` falls back to the day's
close), end-of-day aggregates per contract, 5 requests per minute, 2 years of
history. There are NO historical greeks or IV on any plan, and no historical
chain-in-one-call: a past chain means one aggregates request per contract,
which the rate limit makes impractical. So this provider serves LIVE chains
(15-minute delayed) and underlying daily history; the archive stays the
history source.

API key: MASSIVE_API_KEY env or providers.json {"massiveApiKey": ...}.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from .base import Provider, Unsupported
from .schema import Capabilities, finalize, make_contract

BASE = "https://api.massive.com"


class RateLimiter:
    """Simple token spacing: at most `per_minute` calls, evenly spaced."""

    def __init__(self, per_minute=5, sleep=time.sleep, clock=time.monotonic):
        self.interval = 60.0 / per_minute
        self._sleep = sleep
        self._clock = clock
        self._last = None

    def wait(self):
        now = self._clock()
        if self._last is not None:
            gap = self.interval - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last = now


def _http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "option-obelisk/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class MassiveProvider(Provider):
    name = "massive"
    capabilities = Capabilities(
        live=True, historical=False, delayed_minutes=15, has_greeks=True,
        has_open_interest=True, has_volume=True, history_depth_days=730,
        license_note="Massive Options Basic: individual use, 15-min delayed, no quotes on starter tiers, no redistribution.",
    )

    def __init__(self, api_key=None, http_get=None, limiter=None):
        self.api_key = api_key
        self._get = http_get or _http_get
        self._limiter = limiter or RateLimiter(5)

    def configured(self):
        return bool(self.api_key)

    def _call(self, path_or_url, **params):
        if not self.api_key:
            raise Unsupported("Massive API key not configured")
        if path_or_url.startswith("http"):
            url = path_or_url
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}apiKey={urllib.parse.quote(self.api_key)}"
        else:
            params["apiKey"] = self.api_key
            url = f"{BASE}{path_or_url}?{urllib.parse.urlencode(params)}"
        self._limiter.wait()
        body = self._get(url)
        if body.get("status") not in ("OK", "DELAYED", None) and "results" not in body:
            raise Unsupported(f"Massive: {body.get('error') or body.get('message') or body.get('status')}")
        return body

    def snapshot_pages(self, symbol, exp_from, exp_to):
        page = self._call(f"/v3/snapshot/options/{symbol.upper()}", limit=250,
                          **{"expiration_date.gte": exp_from.isoformat(),
                             "expiration_date.lte": exp_to.isoformat()})
        results = list(page.get("results", []))
        while page.get("next_url"):
            page = self._call(page["next_url"])
            results.extend(page.get("results", []))
        return results

    def chain(self, symbol, asof=None, *, max_expirations=6, min_dte=1, max_dte=120):
        if asof is not None and asof != date.today():
            raise Unsupported("Massive has no historical chain snapshots (per-contract aggregates only)")
        today = datetime.now(timezone.utc).date()
        from datetime import timedelta
        results = self.snapshot_pages(symbol, today + timedelta(days=min_dte),
                                      today + timedelta(days=max_dte))
        if not results:
            raise Unsupported(f"Massive returned no contracts for {symbol.upper()}")
        return snapshot_to_payload(results, symbol, today, self.name, self.capabilities,
                                   max_expirations)

    def underlying_history(self, symbol, start, end):
        body = self._call(f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                          adjusted="true", sort="asc", limit=50000)
        out = []
        for bar in body.get("results", []):
            d = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).date().isoformat()
            out.append((d, float(bar["c"])))
        return out


def snapshot_to_payload(results, symbol, today, provider, capabilities, max_expirations=6):
    """/v3/snapshot/options results -> schema v2 payload."""
    chains = {}
    price = None
    last_ts = None
    for r in results:
        det = r.get("details", {})
        exp = det.get("expiration_date")
        if not exp:
            continue
        quote = r.get("last_quote") or {}
        day = r.get("day") or {}
        trade = r.get("last_trade") or {}
        ts = None
        for key in (trade.get("sip_timestamp"), quote.get("last_updated")):
            if key:
                ts = datetime.fromtimestamp(int(key) / 1e9, tz=timezone.utc).isoformat()
                break
        c = make_contract(
            det.get("strike_price"), quote.get("bid"), quote.get("ask"),
            trade.get("price") or day.get("close"), day.get("volume"), r.get("open_interest"),
            r.get("implied_volatility"), ts, r.get("greeks"))
        if c is None:
            continue
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts
        ua = r.get("underlying_asset") or {}
        if price is None and ua.get("price"):
            price = float(ua["price"])
        side = "calls" if det.get("contract_type") == "call" else "puts"
        chains.setdefault(exp, {"calls": [], "puts": []})[side].append(c)
    if price is None:
        # starter plans may omit underlying price: infer from parity at the
        # strike with the smallest |call - put|
        best = None
        for exp, sides in chains.items():
            puts = {p["strike"]: p for p in sides["puts"]}
            for call in sides["calls"]:
                put = puts.get(call["strike"])
                if put and call["mid"] and put["mid"]:
                    diff = abs(call["mid"] - put["mid"])
                    if best is None or diff < best[0]:
                        best = (diff, call["strike"] + call["mid"] - put["mid"])
        if best is None:
            raise Unsupported("Massive snapshot carries no underlying price and parity inference failed")
        price = round(best[1], 2)
    exps = sorted(chains)
    if len(exps) > max_expirations:
        step = (len(exps) - 1) / (max_expirations - 1)
        keep = sorted({round(i * step) for i in range(max_expirations)})
        chains = {exps[i]: chains[exps[i]] for i in keep}
    payload = {
        "symbol": symbol.upper(),
        "price": price,
        "underlyingClose": price,
        "atmIv": None,
        "chains": chains,
        "lastTradeAt": last_ts,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
    return finalize(payload, provider=provider, asof=today.isoformat(), capabilities=capabilities)
