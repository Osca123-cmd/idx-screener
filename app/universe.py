"""IDX ticker universe (data/universe.csv) and Yahoo symbol mapping."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app import config


@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    sector: str
    industry: str
    is_financial: bool


def yahoo_symbol(ticker: str) -> str:
    return f"{ticker.upper()}.JK"


def from_yahoo(symbol: str) -> str:
    return symbol.upper().removesuffix(".JK")


def load(path: str | None = None) -> list[Stock]:
    df = pd.read_csv(path or config.get().universe, dtype=str, keep_default_na=False)
    return [
        Stock(r.ticker, r.name, r.sector, r.industry, r.is_financial == "1")
        for r in df.itertuples(index=False)
    ]


def by_ticker(stocks: list[Stock]) -> dict[str, Stock]:
    return {s.ticker: s for s in stocks}
