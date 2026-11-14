"""Data platform (roadmap v2, Phase 1).

Every market-data read goes through DataService.get_chain(symbol, asof):
  asof None  -> live providers in order (Massive if a key is configured, yfinance)
  asof date  -> point-in-time providers (local archive, then DoltHub)
and comes back in one normalized shape (schema.py) stamped with `provider`,
`asof`, the risk-free rate for each expiry and the dividend yield.

The archive (archive.py) is the primary history; providers are swappable so a
paid feed later is one more module here, not a rewrite.
"""
