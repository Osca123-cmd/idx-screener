"""Data source interfaces. Swap in a paid API (e.g. Sectors.app) by implementing these."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass
class FetchResult:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)  # ticker -> OHLCV (lowercase cols, DatetimeIndex)
    failed: dict[str, str] = field(default_factory=dict)           # ticker -> reason


class PriceSource(Protocol):
    def fetch_ohlcv(self, tickers: list[str], start: str | None = None, period: str | None = None) -> FetchResult:
        """Daily bars for IDX tickers (no suffix). Either start (YYYY-MM-DD) or period ('2y')."""


class FundamentalSource(Protocol):
    def fetch_fundamentals(self, tickers: list[str]) -> dict[str, dict]:
        """ticker -> {'eps', 'bvps', 'per', 'pbv', 'roe' (fraction), 'market_cap'} in IDR.
        Tickers without data are simply absent."""
