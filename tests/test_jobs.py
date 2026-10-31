"""Smoke test of the whole daily scan against fake data sources (no network)."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from app import config, jobs, store, watch
from app.data.base import FetchResult
from tests.helpers import frame, wave_downtrend, wave_uptrend

JKT = jobs.JAKARTA
LAST = pd.Timestamp("2026-10-30")  # a Friday


def ending(df, last=LAST):
    df = df.copy()
    df.index = pd.bdate_range(end=last, periods=len(df))
    return df


def breakout():
    up = wave_uptrend(n=300, drift=0.002)
    level = up["high"].iloc[-20:].max()
    c = level * 1.03
    bar = pd.DataFrame({"open": [up["close"].iloc[-1]], "high": [c * 1.005], "low": [up["close"].iloc[-1] * 0.995],
                        "close": [c], "volume": [3_000_000.0]}, index=[up.index[-1] + pd.offsets.BDay()])
    return pd.concat([up, bar])


UNIVERSE = {
    "UPPP": ("Energy", breakout()),
    "UPP2": ("Energy", wave_uptrend(n=301, drift=0.0015)),
    "DOWN": ("Energy", wave_downtrend(n=301)),
    "THIN": ("Energy", frame(np.linspace(1000, 1100, 301), volume=100)),
    "STAL": ("Energy", wave_uptrend(n=301)),
    "BANK": ("Finance", wave_uptrend(n=301, base=5000)),
    "FAIL": ("Energy", None),
}


class FakePrices:
    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, tickers, start=None, period=None, progress=None):
        self.calls.append((sorted(tickers), start, period))
        res = FetchResult()
        for t in tickers:
            sector, df = UNIVERSE[t]
            if df is None:
                res.failed[t] = "PermanentError: not found"
                continue
            last = LAST - pd.offsets.BDay(6) if t == "STAL" else LAST
            df = ending(df, last)
            if start:
                df = df[df.index >= start]
            res.frames[t] = df
        return res


class FakeFunds:
    def fetch_fundamentals(self, tickers):
        base = {t: {"eps": 100.0, "bvps": 1000.0, "per": None, "pbv": None, "roe": 0.1, "market_cap": 1e12} for t in tickers}
        base.pop("FAIL", None)
        return base


@pytest.fixture
def cfg(tmp_path):
    c = config.load()
    c.database = str(tmp_path / "s.db")
    c.universe = str(tmp_path / "u.csv")
    c.filters.min_avg_value_idr = 500_000_000  # synthetic frames trade ~Rp 1 M/day
    rows =["ticker,name,sector,industry,is_financial"]
    for t, (sector, _) in UNIVERSE.items():
        rows.append(f"{t},PT {t},{sector},{'Major Banks' if t == 'BANK' else 'x'},{1 if t == 'BANK' else 0}")
    (tmp_path / "u.csv").write_text("\n".join(rows) + "\n")
    return c


def run(cfg, now=datetime(2026, 10, 30, 17, 0, tzinfo=JKT), **kw):
    return jobs.daily_scan(cfg, source=FakePrices(), fund_source=FakeFunds(), now=now, **kw)


def test_daily_scan_end_to_end(cfg):
    sid = run(cfg)
    conn = store.connect(cfg.database)
    scan = store.get_scan(conn, sid)
    assert scan["status"] == "done" and scan["scan_date"] == "2026-10-30"
    assert scan["n_failed"] == 1 and scan["n_stale"] == 1 and scan["n_filtered"] == 1
    results = {r["ticker"]: r for r in store.scan_results(conn, sid)}
    assert set(results) == {"UPPP", "UPP2", "DOWN", "BANK"}
    up = results["UPPP"]
    assert up["trend"] == "NAIK" and up["setup_type"] == "Breakout" and up["score"] >= 70
    assert up["entry"] > up["sl"] and up["tp2"] >= up["tp1"] > up["entry"]
    assert up["detail"]["checklist"] and up["valuation_label"]
    assert results["DOWN"]["trend"] == "TURUN"
    assert "bank" in results["BANK"]["valuation_reason"]
    errors = {(e["ticker"], e["stage"]) for e in store.scan_errors(conn, sid)}
    assert ("FAIL", "fetch") in errors and ("STAL", "stale") in errors


def test_second_scan_same_session_is_no_session_and_incremental(cfg):
    run(cfg)
    src = FakePrices()
    sid = jobs.daily_scan(cfg, source=src, fund_source=FakeFunds(), now=datetime(2026, 10, 31, 9, 0, tzinfo=JKT))
    conn = store.connect(cfg.database)
    assert store.get_scan(conn, sid)["status"] == "no_session"
    assert all(start is not None for tickers, start, _ in src.calls if "UPPP" in tickers)  # incremental fetch
    assert store.latest_scan(conn)["status"] == "done"  # screener still shows the last real scan


def test_unclosed_bar_dropped_during_market_hours(cfg):
    sid = run(cfg, now=datetime(2026, 10, 30, 11, 0, tzinfo=JKT))
    conn = store.connect(cfg.database)
    assert store.get_scan(conn, sid)["scan_date"] == "2026-10-29"


def test_lock_prevents_parallel_scans(cfg):
    with jobs.scan_lock(cfg.database):
        with pytest.raises(jobs.ScanLocked):
            run(cfg)


def test_drop_unclosed_bar_helper():
    df = frame([1, 2, 3], start="2026-10-28")
    assert len(jobs.drop_unclosed_bar(df, datetime(2026, 10, 30, 10, 0, tzinfo=JKT), "16:15")) == 2
    assert len(jobs.drop_unclosed_bar(df, datetime(2026, 10, 30, 16, 30, tzinfo=JKT), "16:15")) == 3


PLAN = {"zone_low": 950, "entry": 970, "sl": 940, "tp1": 1050, "tp2": 1100}


def ohlc(rows):
    idx = pd.bdate_range("2026-10-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)


@pytest.mark.parametrize("rows,expected", [
    ([(1000, 1010, 990, 1000)], watch.WAITING),
    ([(1000, 1000, 965, 980)], watch.IN_TRADE),
    ([(1000, 1000, 965, 980), (980, 1060, 975, 1040)], watch.TP1),
    ([(1000, 1000, 965, 980), (980, 1060, 975, 1040), (1040, 1110, 1030, 1100)], watch.TP2),
    ([(1000, 1000, 965, 980), (980, 1060, 975, 1040), (1040, 1045, 960, 965), (965, 1110, 960, 1100)], watch.TP1),
    ([(1000, 1000, 965, 980), (980, 985, 935, 950)], watch.SL),
    ([(1000, 1000, 945, 948)], watch.INVALID),
    ([(1000, 1060, 935, 1000)], watch.SL),  # same bar SL and TP1 -> SL
])
def test_watch_status(rows, expected):
    assert watch.status(PLAN, ohlc(rows))["status"] == expected
