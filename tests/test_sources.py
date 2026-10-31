"""Data adapters, tested against responses recorded on 2026-10-31 (no network)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.data import tradingview, yahoo

FIX = Path(__file__).parent / "fixtures"
NOT_FOUND = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}


def load(name):
    return json.loads((FIX / name).read_text())


def test_parse_chart_recorded_bbca():
    df = yahoo.parse_chart(load("chart_bbca_5d.json"))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 5
    assert df.index[-1] == pd.Timestamp("2026-10-30")
    assert df["close"].iloc[-1] == 4980.0
    assert df.index.is_monotonic_increasing


def test_parse_chart_not_found_is_permanent():
    with pytest.raises(yahoo.PermanentError):
        yahoo.parse_chart(NOT_FOUND)


def test_fetch_ohlcv_collects_failures_without_retrying_permanent(monkeypatch):
    src = yahoo.YahooSource(retries=3, backoff=0, workers=2)
    calls = []

    def fake_get(symbol, params):
        calls.append(symbol)
        if symbol == "BBCA.JK":
            return load("chart_bbca_5d.json")
        if symbol == "FLKY.JK" and calls.count("FLKY.JK") < 2:
            raise TimeoutError("slow")
        if symbol == "FLKY.JK":
            return load("chart_bbca_5d.json")
        return NOT_FOUND

    monkeypatch.setattr(src, "_get", fake_get)
    res = src.fetch_ohlcv(["BBCA", "ZZZZ", "FLKY"], period="5d")
    assert set(res.frames) == {"BBCA", "FLKY"}
    assert "ZZZZ" in res.failed and "PermanentError" in res.failed["ZZZZ"]
    assert calls.count("ZZZZ.JK") == 1      # permanent: no retry
    assert calls.count("FLKY.JK") == 2      # transient: retried once


def test_start_param_uses_period1():
    p = yahoo._params("2026-10-01", None)
    assert "period1" in p and "range" not in p
    assert yahoo._params(None, "2y")["range"] == "2y"


def test_parse_tradingview_scan():
    data = tradingview.parse_scan(load("tv_fundamentals.json"))
    assert data["BBCA"]["eps"] == pytest.approx(482.8022)
    assert data["BBCA"]["roe"] == pytest.approx(0.212047, rel=1e-3)
    assert data["GOTO"]["eps"] < 0 and data["GOTO"]["per"] is None


def test_tradingview_filters_to_requested(monkeypatch):
    src = tradingview.TradingViewFundamentals()
    monkeypatch.setattr(src, "_post", lambda body: load("tv_fundamentals.json"))
    assert set(src.fetch_fundamentals(["BBCA", "TLKM", "XXXX"])) == {"BBCA", "TLKM"}


@pytest.mark.network
def test_live_sources():
    res = yahoo.YahooSource().fetch_ohlcv(["BBCA", "TLKM"], period="1mo")
    assert set(res.frames) == {"BBCA", "TLKM"}
    f = tradingview.TradingViewFundamentals().fetch_fundamentals(["BBCA"])
    assert f["BBCA"]["eps"] > 0
