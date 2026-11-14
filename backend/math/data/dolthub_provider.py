"""DoltHub `post-no-preference/options`: free end-of-day chains with greeks.

Table option_chain(date, act_symbol, expiration, strike, call_put, bid, ask,
vol, delta, gamma, theta, vega, rho). No volume, no open interest, no
underlying price: the underlying close comes from another provider or the
archive, and liquidity is marked unknown so the Node gates do not drop the
whole chain. Read-only SQL over HTTPS; one request per (symbol, date).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from .base import Provider, Unsupported
from .schema import Capabilities, finalize, make_contract

API = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master"


def _query(sql, opener=None, timeout=60):
    url = f"{API}?q={urllib.parse.quote(sql)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "option-obelisk/2.0"})
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("query_execution_status") not in ("Success", "RowLimit"):
        raise Unsupported(f"DoltHub: {body.get('query_execution_message', 'query failed')}")
    return body.get("rows", [])


class DoltHubProvider(Provider):
    name = "dolthub"
    capabilities = Capabilities(
        live=False, historical=True, delayed_minutes=-1, has_greeks=True,
        has_open_interest=False, has_volume=False, history_depth_days=365 * 5,
        license_note="Community dataset on DoltHub (post-no-preference/options); verify terms before redistribution.",
    )

    def __init__(self, query=None, underlying_close=None):
        self._query = query or _query
        # callable(symbol, asof) -> close, supplied by the service (archive/yfinance)
        self._underlying_close = underlying_close

    def chain(self, symbol, asof=None, *, max_expirations=6, min_dte=1, max_dte=120):
        if asof is None:
            raise Unsupported("DoltHub is end-of-day history only")
        sym = symbol.upper()
        sql = ("SELECT expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega, rho "
               f"FROM option_chain WHERE act_symbol = '{sym}' AND `date` = '{asof.isoformat()}' "
               "ORDER BY expiration, strike")
        rows = self._query(sql)
        if not rows:
            raise Unsupported(f"DoltHub has no chain for {sym} on {asof.isoformat()}")
        return rows_to_payload(rows, sym, asof, self._close_for(sym, asof, rows),
                               self.name, self.capabilities, max_expirations, min_dte, max_dte)

    def _close_for(self, sym, asof, rows):
        if self._underlying_close:
            close = self._underlying_close(sym, asof)
            if close:
                return float(close)
        # fallback: put-call parity at the strike where |call - put| is smallest
        best = None
        by_key = {}
        for r in rows:
            by_key.setdefault((r["expiration"], float(r["strike"])), {})[r["call_put"]] = r
        for (exp, k), sides in by_key.items():
            if "Call" in sides and "Put" in sides:
                c = (float(sides["Call"]["bid"] or 0) + float(sides["Call"]["ask"] or 0)) / 2
                p = (float(sides["Put"]["bid"] or 0) + float(sides["Put"]["ask"] or 0)) / 2
                if c > 0 and p > 0 and (best is None or abs(c - p) < best[0]):
                    best = (abs(c - p), k + c - p)
        if best is None:
            raise Unsupported("cannot infer underlying price from the DoltHub chain")
        return round(best[1], 2)


def rows_to_payload(rows, sym, asof, close, provider, capabilities,
                    max_expirations=6, min_dte=1, max_dte=120):
    chains = {}
    for r in rows:
        exp = str(r["expiration"])[:10]
        dte = (date.fromisoformat(exp) - asof).days
        if dte < min_dte or dte > max_dte:
            continue
        greeks = {k: r.get(k) for k in ("delta", "gamma", "theta", "vega", "rho")}
        c = make_contract(r["strike"], r.get("bid"), r.get("ask"), None, None, None,
                          r.get("vol"), None, greeks)
        if c is None:
            continue
        side = "calls" if str(r["call_put"]).lower().startswith("c") else "puts"
        chains.setdefault(exp, {"calls": [], "puts": []})[side].append(c)
    exps = sorted(chains)
    if len(exps) > max_expirations:
        step = (len(exps) - 1) / (max_expirations - 1)
        keep = sorted({round(i * step) for i in range(max_expirations)})
        chains = {exps[i]: chains[exps[i]] for i in keep}
    payload = {
        "symbol": sym,
        "price": close,
        "underlyingClose": close,
        "atmIv": None,
        "chains": chains,
        "lastTradeAt": None,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
    return finalize(payload, provider=provider, asof=asof.isoformat(), capabilities=capabilities)
