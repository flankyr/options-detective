"""Built-in strategies. Any Strategy subclass found in this package is
runnable from the Research tab by its `name`."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from sim.strategy import Strategy


def registry() -> dict:
    out = {}
    for mod in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{mod.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Strategy) and cls is not Strategy and getattr(cls, "name", "base") != "base":
                out[cls.name] = cls
    return out
