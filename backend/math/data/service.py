"""DataService: one entry point, ordered providers, point-in-time semantics."""

from __future__ import annotations

import json
import os
from datetime import date

import american
import archive
import volforecast
import volmodel

from .archive_provider import ArchiveProvider
from .base import Provider, Unsupported
from .dolthub_provider import DoltHubProvider
from .massive_provider import MassiveProvider
from .rates import RateCurve
from .schema import validate
from .yfinance_provider import YFinanceProvider

CONFIG_FILE = "providers.json"
DEFAULT_LIVE_ORDER = ["massive", "yfinance"]
DEFAULT_HIST_ORDER = ["archive", "dolthub"]


def load_config(root):
    try:
        with open(os.path.join(root, CONFIG_FILE), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(root, patch):
    cfg = {**load_config(root)}
    for k, v in patch.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    os.makedirs(root, exist_ok=True)
    tmp = os.path.join(root, CONFIG_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, os.path.join(root, CONFIG_FILE))
    try:
        os.chmod(os.path.join(root, CONFIG_FILE), 0o600)
    except OSError:
        pass
    return cfg


class DataService:
    def __init__(self, root=None, providers=None, rates=None, config=None, today=None):
        self.root = root or os.path.dirname(archive.default_root())
        self.config = config if config is not None else load_config(self.root)
        self.today = today or date.today
        if providers is None:
            arch = ArchiveProvider(os.path.join(self.root, "archive"))
            providers = [
                MassiveProvider(api_key=os.environ.get("MASSIVE_API_KEY") or self.config.get("massiveApiKey")),
                YFinanceProvider(),
                arch,
                DoltHubProvider(underlying_close=lambda s, d: self._close_from_archive(arch, s, d)),
            ]
        self.providers: dict[str, Provider] = {p.name: p for p in providers}
        self.rates = rates or RateCurve(self.root)

    # ---- ordering -------------------------------------------------------
    def live_order(self):
        order = self.config.get("liveOrder") or DEFAULT_LIVE_ORDER
        return [n for n in order if n in self.providers and self.providers[n].capabilities.live]

    def hist_order(self):
        order = self.config.get("historicalOrder") or DEFAULT_HIST_ORDER
        return [n for n in order if n in self.providers and self.providers[n].capabilities.historical]

    @staticmethod
    def _close_from_archive(arch, symbol, asof):
        try:
            df = archive.read_snapshot(arch.root, symbol, asof)
            if df is not None and len(df):
                return float(df.iloc[0]["underlyingClose"])
        except Exception:
            return None
        return None

    # ---- main entry -----------------------------------------------------
    def get_chain(self, symbol, asof=None, *, max_expirations=6, min_dte=1, max_dte=120,
                  provider=None):
        """Normalized chain for `symbol` as of `asof` (None = live).

        Tries providers in order; the first that is configured and succeeds
        wins. Attempts are reported in payload["attempts"] so a fallback is
        never silent.
        """
        if isinstance(asof, str):
            asof = date.fromisoformat(asof)
        if asof is not None and asof >= self.today():
            asof = None  # today or future: that is a live request
        names = [provider] if provider else (self.live_order() if asof is None else self.hist_order())
        attempts = []
        for name in names:
            p = self.providers.get(name)
            if p is None:
                attempts.append({"provider": name, "error": "unknown provider"})
                continue
            if not p.configured():
                attempts.append({"provider": name, "error": "not configured"})
                continue
            try:
                payload = p.chain(symbol, asof, max_expirations=max_expirations,
                                  min_dte=min_dte, max_dte=max_dte)
            except Unsupported as exc:
                attempts.append({"provider": name, "error": str(exc)})
                continue
            except Exception as exc:  # network/parse failure: try the next source
                attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            problems = validate(payload)
            if problems:
                attempts.append({"provider": name, "error": "invalid payload: " + "; ".join(problems)})
                continue
            payload["attempts"] = attempts
            payload["rates"] = self.rates.rates_for(payload["expirations"],
                                                    date.fromisoformat(payload["asof"]))
            if payload.get("dividendYield") is None and asof is None:
                payload["dividendYield"] = self.providers["yfinance"].dividend_yield(
                    symbol, payload["price"]) if "yfinance" in self.providers else None
            # Phase 2: solve our own IVs and fit one smile per expiry. A failure
            # here must not hide the chain: report it and return the raw payload.
            payload["exerciseStyle"] = american.exercise_style(symbol)
            try:
                volmodel.enrich(payload)
            except Exception as exc:
                payload["volModelError"] = f"{type(exc).__name__}: {exc}"
            return payload
        mode = "live" if asof is None else f"historical ({asof.isoformat()})"
        detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts) or "no providers"
        raise Unsupported(f"no {mode} source could serve {symbol.upper()}: {detail}")

    # ---- history + forecasts (Phase 3) -------------------------------------------
    def underlying_history(self, symbol, days=400, end=None):
        """Daily closes for the last `days`: live providers first (deep history),
        the archive's own closes as the offline fallback."""
        from datetime import timedelta
        end = end or self.today()
        start = end - timedelta(days=int(days * 1.6))  # calendar -> trading-day slack
        attempts = []
        order = [n for n in self.live_order()] + [n for n in self.hist_order() if n == "archive"]
        for name in order:
            p = self.providers.get(name)
            if p is None or not p.configured():
                continue
            try:
                rows = p.underlying_history(symbol, start, end)
            except Unsupported as exc:
                attempts.append({"provider": name, "error": str(exc)})
                continue
            except Exception as exc:
                attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if rows:
                return {"symbol": symbol.upper(), "provider": name, "rows": rows, "attempts": attempts}
        raise Unsupported(f"no source has price history for {symbol.upper()}: "
                          + "; ".join(f"{a['provider']}: {a['error']}" for a in attempts))

    def forecast(self, symbol, horizon_days=21, method="har", iv_atm=None):
        """Realized-vol forecast at `horizon_days` trading days + VRP against
        `iv_atm` (pass the chain's ATM vol at the matching expiry)."""
        hist = self.underlying_history(symbol)
        closes = [c for _, c in hist["rows"]]
        fc = volforecast.forecast(closes, int(horizon_days), method)
        fc["symbol"] = symbol.upper()
        fc["historyProvider"] = hist["provider"]
        fc["asOf"] = hist["rows"][-1][0]
        # VRP history: archived ATM IV minus the trailing 21d realized vol on that day
        # (a proxy for IV - forecast; good enough to z-score once ~20 days exist)
        arch = self.providers.get("archive")
        history = []
        if arch is not None:
            closes_by_date = dict(hist["rows"])
            dates = sorted(closes_by_date)
            import numpy as np
            for d, iv in arch.atm_iv_history(symbol):
                idx = [i for i, dd in enumerate(dates) if dd <= d]
                if len(idx) >= 22:
                    window = np.array([closes_by_date[dates[i]] for i in idx[-22:]])
                    rv = volforecast.realized_vol(np.diff(np.log(window)), 21)
                    history.append(iv - rv)
        fc["vrp"] = volforecast.vrp(iv_atm, fc["sigma"], history)
        return fc

    def status(self):
        arch = self.providers.get("archive")
        return {
            "root": self.root,
            "liveOrder": self.live_order(),
            "historicalOrder": self.hist_order(),
            "providers": [p.describe() for p in self.providers.values()],
            "rates": self.rates.describe(),
            "config": {k: ("set" if "Key" in k else v) for k, v in self.config.items()},
            "archiveSymbols": archive.status(arch.root) if arch else {},
        }

    def set_config(self, patch):
        allowed = {"massiveApiKey", "liveOrder", "historicalOrder"}
        clean = {k: v for k, v in patch.items() if k in allowed}
        self.config = save_config(self.root, clean)
        if "massiveApiKey" in clean and "massive" in self.providers:
            self.providers["massive"].api_key = clean["massiveApiKey"]
        return self.status()
