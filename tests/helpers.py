"""Synthetic OHLCV builders for tests."""
from __future__ import annotations

import numpy as np
import pandas as pd


def frame(closes, spread=0.01, volume=1_000_000, start="2024-01-01", opens=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    opens = np.asarray(opens, dtype=float) if opens is not None else np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    vols = np.full(len(closes), volume, dtype=float) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)


def wave_uptrend(n=300, base=1000.0, drift=0.002, amp=0.04, period=20) -> pd.DataFrame:
    """Rising trend with regular pullbacks -> clear HH/HL swings."""
    t = np.arange(n)
    closes = base * (1 + drift) ** t * (1 + amp * np.sin(2 * np.pi * t / period))
    return frame(closes)


def wave_downtrend(n=300, base=3000.0, drift=0.002, amp=0.04, period=20) -> pd.DataFrame:
    t = np.arange(n)
    closes = base * (1 - drift) ** t * (1 + amp * np.sin(2 * np.pi * t / period))
    return frame(closes)
