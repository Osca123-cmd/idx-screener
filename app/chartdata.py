"""Chart payload shared by the local web API and the static site generator."""
from __future__ import annotations

import pandas as pd

from app import indicators


def payload(prices: pd.DataFrame, bars: int = 260) -> dict:
    dfi = indicators.add_all(prices).iloc[-bars:]
    t = [d.strftime("%Y-%m-%d") for d in dfi.index]

    def series(col):
        return [{"time": d, "value": round(float(v), 2)} for d, v in zip(t, dfi[col]) if v == v]

    return {
        "candles": [{"time": d, "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for d, r in zip(t, dfi.itertuples())],
        "volume": [{"time": d, "value": r.volume, "up": r.close >= r.open} for d, r in zip(t, dfi.itertuples())],
        "ema20": series("ema20"), "ema50": series("ema50"), "ema200": series("ema200"),
    }
