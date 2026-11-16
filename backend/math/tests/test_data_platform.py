import json
from datetime import date, datetime, timezone

import pytest

import archive
from data.archive_provider import ArchiveProvider
from data.base import Provider, Unsupported
from data.dolthub_provider import DoltHubProvider, rows_to_payload
from data.massive_provider import MassiveProvider, RateLimiter, snapshot_to_payload
from data.rates import RateCurve, interpolate, parse_treasury_csv
from data.schema import Capabilities, finalize, make_contract, validate
from data.service import DataService
import datasvc

ASOF = date(2026, 11, 13)


def fake_live_payload(symbol="SPY", price=500.0):
    return {
        "symbol": symbol, "price": price, "atmIv": 0.18,
        "fetchedAt": "2026-11-13T21:30:00+00:00",
        "expirations": ["2026-11-20", "2026-12-18"],
        "chains": {
            "2026-11-20": {
                "calls": [{"strike": 500, "bid": 5.0, "ask": 5.2, "mid": 5.1, "volume": 1000,
                           "openInterest": 5000, "impliedVolatility": 0.17, "spreadPct": 0.039,
                           "timestamp": "2026-11-13T20:59:00+00:00"}],
                "puts": [{"strike": 500, "bid": 4.8, "ask": 5.0, "mid": 4.9, "volume": 900,
                          "openInterest": 4000, "impliedVolatility": 0.19, "spreadPct": 0.04,
                          "timestamp": None}],
            },
            "2026-12-18": {"calls": [], "puts": [
                {"strike": 480, "bid": 6.0, "ask": 6.4, "mid": 6.2, "volume": 10,
                 "openInterest": 20, "impliedVolatility": None, "spreadPct": None, "timestamp": None}]},
        },
    }


# ---- schema ---------------------------------------------------------------

def test_make_contract_mid_and_spread_rules():
    c = make_contract(100, 1.0, 1.2, 1.1, 10, 20, 0.3, None, {"delta": 0.5, "gamma": None})
    assert c["mid"] == 1.1 and c["spreadPct"] == round(0.2 / 1.1, 4)
    assert c["greeks"] == {"delta": 0.5, "gamma": None}
    bookless = make_contract(100, 0, 0, 2.5, None, None, -1)
    assert bookless["mid"] == 2.5 and bookless["spreadPct"] is None
    assert bookless["volume"] is None and bookless["impliedVolatility"] is None
    assert make_contract(0, 1, 2) is None
    assert make_contract("nan", 1, 2) is None


def test_finalize_and_validate():
    caps = Capabilities(True, False, 15, False, True, True, 0, "test")
    p = fake_live_payload()
    p["chains"]["2026-11-20"]["calls"].insert(0, {**p["chains"]["2026-11-20"]["calls"][0], "strike": 510})
    out = finalize(p, provider="t", asof="2026-11-13", capabilities=caps, dividend_yield=0.012)
    assert validate(out) == []
    assert out["liquidityKnown"] is True and out["provider"] == "t"
    assert [c["strike"] for c in out["chains"]["2026-11-20"]["calls"]] == [500, 510]
    assert validate({"symbol": "X"}) != []


# ---- archive provider ----------------------------------------------------

def test_archive_provider_roundtrip(tmp_path):
    archive.run(["SPY"], str(tmp_path), lambda s, **k: fake_live_payload(s),
                now=datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc))
    prov = ArchiveProvider(str(tmp_path))
    assert prov.available_dates("SPY") == ["2026-11-13"]
    p = prov.chain("spy", ASOF, max_dte=400)
    assert validate(p) == []
    assert p["provider"] == "archive" and p["asof"] == "2026-11-13"
    assert p["price"] == 500.0 and p["liquidityKnown"] is True
    assert p["expirations"] == ["2026-11-20", "2026-12-18"]
    with pytest.raises(Unsupported):
        prov.chain("SPY", None)
    with pytest.raises(Unsupported):
        prov.chain("SPY", date(2026, 1, 2))


# ---- dolthub provider ----------------------------------------------------

DOLT_ROWS = [
    {"expiration": "2026-12-18", "strike": "500.00", "call_put": "Call", "bid": "12.00", "ask": "12.40",
     "vol": "0.1800", "delta": "0.52", "gamma": "0.01", "theta": "-0.05", "vega": "0.6", "rho": "0.2"},
    {"expiration": "2026-12-18", "strike": "500.00", "call_put": "Put", "bid": "10.00", "ask": "10.40",
     "vol": "0.1900", "delta": "-0.48", "gamma": "0.01", "theta": "-0.05", "vega": "0.6", "rho": "-0.2"},
    {"expiration": "2026-12-18", "strike": "520.00", "call_put": "Call", "bid": "3.00", "ask": "3.30",
     "vol": "0.1700", "delta": "0.25", "gamma": "0.01", "theta": "-0.04", "vega": "0.5", "rho": "0.1"},
]


def test_dolthub_rows_to_payload_marks_liquidity_unknown():
    p = rows_to_payload(DOLT_ROWS, "SPY", ASOF, 502.0, "dolthub", DoltHubProvider.capabilities)
    assert validate(p) == []
    assert p["liquidityKnown"] is False
    call = p["chains"]["2026-12-18"]["calls"][0]
    assert call["volume"] is None and call["openInterest"] is None
    assert call["greeks"]["delta"] == 0.52 and call["impliedVolatility"] == 0.18
    assert p["atmIv"] == pytest.approx(0.185)


def test_dolthub_provider_infers_close_by_parity_when_no_source():
    prov = DoltHubProvider(query=lambda sql: DOLT_ROWS)
    p = prov.chain("SPY", ASOF)
    # parity at 500: C - P = 12.2 - 10.2 = 2.0 -> S ~ 502
    assert p["price"] == pytest.approx(502.0)
    prov2 = DoltHubProvider(query=lambda sql: DOLT_ROWS, underlying_close=lambda s, d: 501.5)
    assert prov2.chain("SPY", ASOF)["price"] == 501.5
    with pytest.raises(Unsupported):
        DoltHubProvider(query=lambda sql: []).chain("SPY", ASOF)
    with pytest.raises(Unsupported):
        prov.chain("SPY", None)


# ---- massive provider ----------------------------------------------------

def massive_results():
    def r(exp, k, typ, bid, ask, iv, oi, under=500.0):
        return {
            "details": {"expiration_date": exp, "strike_price": k, "contract_type": typ,
                        "ticker": f"O:SPY{exp.replace('-', '')[2:]}{typ[0].upper()}{int(k * 1000):08d}"},
            "last_quote": {"bid": bid, "ask": ask, "last_updated": 1763067540000000000},
            "day": {"volume": 1500, "close": (bid + ask) / 2},
            "open_interest": oi,
            "implied_volatility": iv,
            "greeks": {"delta": 0.5 if typ == "call" else -0.5, "gamma": 0.01, "theta": -0.1, "vega": 0.4},
            "underlying_asset": {"price": under, "ticker": "SPY"},
        }
    return [r("2026-11-20", 500, "call", 5.0, 5.2, 0.17, 5000),
            r("2026-11-20", 500, "put", 4.8, 5.0, 0.19, 4000),
            r("2026-12-18", 510, "call", 6.0, 6.3, 0.16, 100)]


def test_massive_snapshot_to_payload():
    p = snapshot_to_payload(massive_results(), "spy", ASOF, "massive", MassiveProvider.capabilities)
    assert validate(p) == []
    assert p["price"] == 500.0 and p["liquidityKnown"] is True
    c = p["chains"]["2026-11-20"]["calls"][0]
    assert c["openInterest"] == 5000 and c["volume"] == 1500 and c["greeks"]["delta"] == 0.5
    assert p["lastTradeAt"].startswith("2025-11-13") or p["lastTradeAt"]  # timestamp decoded


def test_massive_price_falls_back_to_parity_without_underlying():
    res = massive_results()
    for r in res:
        r["underlying_asset"] = {"ticker": "SPY"}
    p = snapshot_to_payload(res, "SPY", ASOF, "massive", MassiveProvider.capabilities)
    assert p["price"] == pytest.approx(500.2)  # 500 + 5.1 - 4.9


def test_massive_pagination_and_key_and_rate_limit():
    calls = []
    sleeps = []
    clock = [0.0]

    def get(url):
        calls.append(url)
        assert "apiKey=k1" in url
        if "cursor=abc" in url:
            return {"status": "OK", "results": massive_results()[2:]}
        return {"status": "OK", "results": massive_results()[:2],
                "next_url": "https://api.massive.com/v3/snapshot/options/SPY?cursor=abc"}

    lim = RateLimiter(5, sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)),
                      clock=lambda: clock[0])
    prov = MassiveProvider(api_key="k1", http_get=get, limiter=lim)
    p = prov.chain("SPY", None, max_dte=60)
    assert len(calls) == 2 and "expiration_date.gte" in calls[0]
    assert sum(len(ch["calls"]) + len(ch["puts"]) for ch in p["chains"].values()) == 3
    assert sleeps and sleeps[0] == pytest.approx(12.0)  # 5/min spacing between the two calls
    assert MassiveProvider(api_key=None).configured() is False
    with pytest.raises(Unsupported):
        MassiveProvider(api_key=None, http_get=get).chain("SPY")
    with pytest.raises(Unsupported):
        prov.chain("SPY", date(2025, 1, 2))


# ---- rates -----------------------------------------------------------------

CSV = """Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"
11/12/2026,3.90,3.91,3.92,3.95,3.97,4.00,4.05,4.10,4.15,4.20,4.30,4.40,4.70,4.80
11/13/2026,3.80,N/A,3.82,3.85,3.87,3.90,3.95,4.00,4.05,4.10,4.20,4.30,4.60,4.70
"""


def test_parse_treasury_takes_latest_row_and_interpolates():
    parsed = parse_treasury_csv(CSV)
    assert parsed["date"] == "2026-11-13"
    assert 1.5 / 12 not in parsed["curve"]  # N/A skipped
    r3m = interpolate(parsed["curve"], 0.25)
    assert r3m == pytest.approx(__import__("math").log1p(0.0385))
    r45d = interpolate(parsed["curve"], 45 / 365)
    assert 0.0380 < __import__("math").expm1(r45d) < 0.0385
    assert interpolate(parsed["curve"], 50) == pytest.approx(__import__("math").log1p(0.047))


def test_rate_curve_caches_and_falls_back(tmp_path):
    fetched = []
    rc = RateCurve(str(tmp_path), fetch=lambda y: (fetched.append(y), CSV)[1],
                   now=lambda: datetime(2026, 11, 14, tzinfo=timezone.utc))
    rates = rc.rates_for(["2026-12-18", "2027-11-19"], ASOF)
    assert set(rates) == {"2026-12-18", "2027-11-19"}
    assert 0.037 < rates["2026-12-18"] < 0.04 and fetched == [2026]
    rc.refresh()
    assert fetched == [2026]  # same day: cached
    rc2 = RateCurve(str(tmp_path), fetch=lambda y: (_ for _ in ()).throw(OSError("offline")),
                    now=lambda: datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert rc2.rate(0.25) > 0.03  # cache file survives the failed refresh
    assert rc2.refresh().get("lastError") == "offline"
    rc3 = RateCurve(str(tmp_path / "empty"), fetch=lambda y: (_ for _ in ()).throw(OSError("offline")))
    assert rc3.describe()["source"] == "fallback" and rc3.rate(1.0) == pytest.approx(__import__("math").log1p(0.04), abs=1e-5)


# ---- service -----------------------------------------------------------------

class Fake(Provider):
    def __init__(self, name, live, hist, result=None, error=None, configured=True):
        self.name = name
        self.capabilities = Capabilities(live, hist, 15, False, True, True, 0, "fake")
        self._result, self._error, self._configured = result, error, configured
        self.calls = []

    def configured(self):
        return self._configured

    def chain(self, symbol, asof=None, **kw):
        self.calls.append((symbol, asof))
        if self._error:
            raise self._error
        return finalize(json.loads(json.dumps(self._result)), provider=self.name,
                        asof=(asof or ASOF).isoformat(), capabilities=self.capabilities)


def make_service(tmp_path, providers):
    rc = RateCurve(str(tmp_path), fetch=lambda y: CSV,
                   now=lambda: datetime(2026, 11, 14, tzinfo=timezone.utc))
    return DataService(root=str(tmp_path), providers=providers, rates=rc, config={},
                       today=lambda: date(2026, 11, 14))


def test_service_live_falls_through_unconfigured_and_failing_providers(tmp_path):
    good = fake_live_payload()
    massive = Fake("massive", True, False, configured=False)
    yfin = Fake("yfinance", True, False, result=good)
    svc = make_service(tmp_path, [massive, yfin, Fake("archive", False, True, error=Unsupported("x"))])
    p = svc.get_chain("SPY")
    assert p["provider"] == "yfinance" and massive.calls == []
    assert p["attempts"] == [{"provider": "massive", "error": "not configured"}]
    assert set(p["rates"]) == set(p["expirations"]) and all(0.03 < r < 0.05 for r in p["rates"].values())


def test_service_historical_order_and_today_is_live(tmp_path):
    good = fake_live_payload()
    arch = Fake("archive", False, True, error=Unsupported("no file"))
    dolt = Fake("dolthub", False, True, result=good)
    live = Fake("yfinance", True, False, result=good)
    svc = make_service(tmp_path, [live, arch, dolt])
    p = svc.get_chain("SPY", "2026-11-13")
    assert p["provider"] == "dolthub" and arch.calls == [("SPY", ASOF)]
    assert p["attempts"][0]["provider"] == "archive"
    p2 = svc.get_chain("SPY", date(2026, 11, 14))  # today -> live path
    assert p2["provider"] == "yfinance"
    with pytest.raises(Unsupported, match="no historical"):
        make_service(tmp_path, [Fake("archive", False, True, error=Unsupported("no file"))]).get_chain("SPY", ASOF)


def test_service_explicit_provider_and_status_and_config(tmp_path):
    good = fake_live_payload()
    svc = make_service(tmp_path, [Fake("yfinance", True, False, result=good),
                                  Fake("massive", True, False, result=good)])
    assert svc.get_chain("SPY", provider="massive")["provider"] == "massive"
    st = svc.status()
    assert st["liveOrder"] == ["massive", "yfinance"] and st["rates"]["source"] == "treasury.gov"
    st2 = svc.set_config({"massiveApiKey": "secret", "liveOrder": ["yfinance", "massive"], "junk": 1})
    assert st2["config"] == {"massiveApiKey": "set", "liveOrder": ["yfinance", "massive"]}
    assert svc.live_order() == ["yfinance", "massive"]
    assert "secret" in open(tmp_path / "providers.json").read()


def test_datasvc_handle_envelopes(tmp_path):
    svc = make_service(tmp_path, [Fake("yfinance", True, False, result=fake_live_payload())])
    assert datasvc.handle({"cmd": "chain", "symbol": "SPY"}, svc)["ok"] is True
    assert datasvc.handle({"cmd": "chain"}, svc) == {"ok": False, "error": "symbol is required"}
    assert datasvc.handle({"cmd": "nope"}, svc)["ok"] is False
    assert datasvc.handle({"cmd": "status"}, svc)["result"]["liveOrder"] == ["yfinance"]
    assert datasvc.handle({"cmd": "rates"}, svc)["result"]["source"] == "treasury.gov"
    assert datasvc.handle({}, svc)["ok"] is False


# ---- Phase 3: history + forecast through the service -------------------------------

class HistFake(Fake):
    def __init__(self, name, closes, dates):
        super().__init__(name, True, False, result=fake_live_payload())
        self._rows = list(zip(dates, closes))

    def underlying_history(self, symbol, start, end):
        return [(d, c) for d, c in self._rows if start.isoformat() <= d <= end.isoformat()]


def test_service_history_and_forecast(tmp_path):
    import numpy as np
    from datetime import timedelta
    rng = np.random.default_rng(3)
    n = 500
    end = date(2026, 11, 13)
    dates = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.012, n))))
    svc = make_service(tmp_path, [HistFake("yfinance", closes, dates), Fake("archive", False, True, error=Unsupported("x"))])
    h = svc.underlying_history("spy", days=100)
    assert h["provider"] == "yfinance" and 90 <= len(h["rows"]) <= 170
    fc = svc.forecast("SPY", 21, "ewma", iv_atm=0.25)
    assert fc["method"] == "ewma" and fc["symbol"] == "SPY" and fc["asOf"] == dates[-1]
    assert 0.1 < fc["sigma"] < 0.4
    assert fc["vrp"]["vrp"] == pytest.approx(0.25 - fc["sigma"], abs=1e-6) and fc["vrp"]["z"] is None
    assert datasvc.handle({"cmd": "forecast", "symbol": "SPY", "horizon": 21, "method": "ewma"}, svc)["ok"]
    with pytest.raises(Unsupported):
        make_service(tmp_path, [Fake("yfinance", True, False, result=fake_live_payload())]).underlying_history("SPY")


def test_archive_rows_with_nan_text_cells_regression():
    """Newer pandas reads empty text cells back from parquet as NaN (a truthy
    float). The provider must treat them as missing, not compare them to strings."""
    nan = float("nan")
    rows = [
        {"symbol": "SPY", "asof": "2026-11-13", "expiration": "2026-11-20", "type": "call", "strike": 500.0,
         "bid": 5.0, "ask": 5.2, "mid": 5.1, "volume": 10, "openInterest": 20, "impliedVolatility": 0.2,
         "spreadPct": 0.04, "lastTradeAt": "2026-11-13T20:59:00+00:00", "underlyingClose": 500.0,
         "atmIv": nan, "fetchedAt": "2026-11-13T21:30:00+00:00"},
        {"symbol": "SPY", "asof": "2026-11-13", "expiration": "2026-11-20", "type": "put", "strike": 500.0,
         "bid": 4.8, "ask": 5.0, "mid": 4.9, "volume": nan, "openInterest": nan, "impliedVolatility": nan,
         "spreadPct": nan, "lastTradeAt": nan, "underlyingClose": 500.0, "atmIv": nan, "fetchedAt": nan},
    ]
    from data.archive_provider import payload_from_rows
    p = payload_from_rows(rows, "SPY", ASOF, "archive", ArchiveProvider.capabilities, max_dte=400)
    assert validate(p) == []
    assert p["lastTradeAt"] == "2026-11-13T20:59:00+00:00"
    put = p["chains"]["2026-11-20"]["puts"][0]
    assert put["timestamp"] is None and put["volume"] is None and put["impliedVolatility"] is None
    assert p["atmIv"] is not None and p["atmIv"] == p["atmIv"]  # recomputed from the chain, not NaN
    import json
    json.dumps(p, allow_nan=False)
