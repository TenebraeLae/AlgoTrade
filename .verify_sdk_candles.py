"""Temporary probe: verify candles API names in the installed SDK. Not part of the package."""

from __future__ import annotations

import inspect

import tinkoff.invest as ti

print("=== Client.market_data (getattr_static) ===")
prop = inspect.getattr_static(ti.Client, "market_data")
print(type(prop))
print(prop)

print("\n=== services module public names ===")
print([n for n in dir(ti.services) if not n.startswith("_")])

print("\n=== schemas module: candles-related names ===")
names = [
    n
    for n in dir(ti.schemas)
    if not n.startswith("_") and any(k in n for k in ("Candle", "Quotation", "Interval", "MoneyValue"))
]
print(names)

print("\n=== MarketDataService attrs ===")
svc = getattr(ti.services, "MarketDataService", None)
print(svc)
if svc is not None:
    print([n for n in dir(svc) if not n.startswith("_")])