"""Yahoo Finance price adapter (unofficial, free) using the public v8 chart endpoint.

Talks to the endpoint directly instead of through yfinance: on this network yfinance's
HTTP client needed ~20s per request while a plain request takes <0.5s.
Prices are split-adjusted but NOT dividend-adjusted, matching what Stockbit/TradingView
show by default, so entry/SL/TP levels line up with the charts you trade from.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.data.base import FetchResult
from app.universe import yahoo_symbol

URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
JAKARTA = timezone(timedelta(hours=7))


class PermanentError(Exception):
    """Symbol does not exist / has no data; retrying will not help."""


def parse_chart(payload: dict) -> pd.DataFrame:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise PermanentError(chart["error"].get("description") or str(chart["error"]))
    result = (chart.get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise PermanentError("no price data")
    q = result["indicators"]["quote"][0]
    idx = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert("Asia/Jakarta").tz_localize(None).normalize()
    df = pd.DataFrame(
        {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]},
        index=idx, dtype=float,
    )
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.empty:
        raise PermanentError("no price data")
    return df


def _params(start: str | None, period: str | None) -> dict:
    p = {"interval": "1d", "events": "split"}
    if start:
        t0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=JAKARTA)
        p["period1"] = int(t0.timestamp())
        p["period2"] = int(datetime.now(JAKARTA).timestamp()) + 86400
    else:
        p["range"] = period or "2y"
    return p


class YahooSource:
    def __init__(self, retries: int = 3, backoff: float = 2.0, workers: int = 8, timeout: float = 30):
        self.retries = retries
        self.backoff = backoff
        self.workers = workers
        self.timeout = timeout

    def _get(self, symbol: str, params: dict) -> dict:
        url = URL.format(symbol=symbol) + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                try:
                    return json.load(e)
                except Exception:
                    raise PermanentError("not found") from e
            raise

    def fetch_one(self, ticker: str, start: str | None = None, period: str | None = None) -> pd.DataFrame:
        params = _params(start, period)
        for attempt in range(1, self.retries + 1):
            try:
                return parse_chart(self._get(yahoo_symbol(ticker), params))
            except PermanentError:
                raise
            except Exception:
                if attempt == self.retries:
                    raise
                time.sleep(self.backoff * attempt)
        raise RuntimeError("unreachable")

    def fetch_ohlcv(self, tickers: list[str], start: str | None = None, period: str | None = None,
                    progress=None) -> FetchResult:
        result = FetchResult()

        def job(t: str):
            try:
                return t, self.fetch_one(t, start, period), None
            except Exception as e:
                return t, None, f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for i, (t, df, err) in enumerate(pool.map(job, tickers), 1):
                if err:
                    result.failed[t] = err
                else:
                    result.frames[t] = df
                if progress:
                    progress(i, len(tickers))
        return result
