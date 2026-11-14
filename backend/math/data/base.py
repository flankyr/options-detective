"""Provider interface. Each source implements this; DataService composes them."""

from __future__ import annotations

from datetime import date

from .schema import Capabilities


class Unsupported(Exception):
    """The provider cannot serve this request (wrong mode, no key, no history)."""


class Provider:
    name: str = "base"
    capabilities: Capabilities

    def configured(self) -> bool:
        """True when the provider has what it needs (keys, files) to be tried."""
        return True

    def chain(self, symbol: str, asof: date | None = None, *, max_expirations: int = 6,
              min_dte: int = 1, max_dte: int = 120) -> dict:
        raise Unsupported(f"{self.name} cannot serve chains")

    def underlying_history(self, symbol: str, start: date, end: date) -> list:
        """[(ISO date, close), ...] ascending, or Unsupported."""
        raise Unsupported(f"{self.name} has no underlying history")

    def atm_iv_history(self, symbol: str) -> list:
        """[(ISO date, atm iv), ...] ascending, or [] when the source keeps none."""
        return []

    def dividend_yield(self, symbol: str, price: float):
        """Trailing-12m cash dividends / price, or None."""
        return None

    def describe(self) -> dict:
        return {"name": self.name, "configured": self.configured(),
                "capabilities": self.capabilities.to_dict()}
