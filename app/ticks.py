"""IDX price fractions (fraksi harga) and rounding."""
from __future__ import annotations

import math

# (upper bound exclusive, tick)
_BANDS = [(200, 1), (500, 2), (2000, 5), (5000, 10), (math.inf, 25)]


def tick_size(price: float) -> int:
    for upper, tick in _BANDS:
        if price < upper:
            return tick
    return 25


def round_to_tick(price: float, mode: str = "nearest") -> float:
    """Round to a valid IDX price. mode: nearest | down | up."""
    if price is None or math.isnan(price) or price <= 0:
        return price
    tick = tick_size(price)
    q = price / tick
    if mode == "down":
        n = math.floor(q + 1e-9)
    elif mode == "up":
        n = math.ceil(q - 1e-9)
    else:
        n = math.floor(q + 0.5)
    return float(max(n, 1) * tick)
