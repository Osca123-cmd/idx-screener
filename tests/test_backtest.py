import pandas as pd
import pytest

from app import backtest, config, indicators, store
from tests.helpers import frame, wave_uptrend

PLAN = {"entry": 100.0, "sl": 90.0, "tp1": 115.0}


def ohlc(rows):
    idx = pd.bdate_range("2026-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)


@pytest.mark.parametrize("rows,outcome,r", [
    ([(105, 106, 101, 105)] * 3, "no_fill", 0.0),
    ([(105, 106, 99, 104), (104, 116, 103, 115)], "win", 1.5),
    ([(105, 106, 99, 104), (104, 105, 89, 90)], "loss", -1.0),
    ([(105, 116, 89, 100)], "loss", -1.0),               # same bar: SL first
    ([(105, 106, 99, 104), (104, 108, 101, 105)], "timeout", 0.5),
])
def test_simulate(rows, outcome, r):
    res = backtest.simulate(PLAN, ohlc(rows))
    assert res["outcome"] == outcome and res["r"] == pytest.approx(r)


def test_fee_reduces_r():
    res = backtest.simulate(PLAN, ohlc([(105, 106, 99, 104), (104, 116, 103, 115)]), fee_pct=0.4)
    assert res["r"] == pytest.approx(1.5 - 0.04)   # 0.4% of 100 = 0.4 = 0.04R on a 10-point risk


def test_candidate_mask_superset_of_uptrend():
    dfi = indicators.add_all(wave_uptrend(400))
    mask = backtest.candidate_mask(dfi, 20)
    assert mask.iloc[-50:].all()


def test_aggregate():
    sig = [
        {"setup": "Breakout", "score": 80, "trend": "NAIK", "outcome": "win", "r": 1.5},
        {"setup": "Breakout", "score": 75, "trend": "NAIK", "outcome": "loss", "r": -1.0},
        {"setup": "Pullback", "score": 55, "trend": "SIDEWAYS", "outcome": "no_fill", "r": 0.0},
    ]
    agg = backtest.aggregate(sig)
    assert agg["by_setup"]["Breakout"]["win_rate"] == 50.0
    assert agg["by_setup"]["Breakout"]["avg_r"] == 0.25
    assert agg["by_setup"]["Pullback"]["fill_rate"] == 0.0 and agg["by_setup"]["Pullback"]["win_rate"] is None
    assert agg["by_score"]["70–100"]["n"] == 2


def test_run_no_lookahead_and_saves(tmp_path):
    cfg = config.load()
    cfg.database = str(tmp_path / "b.db")
    cfg.filters.min_avg_value_idr = 1
    conn = store.connect(cfg.database)
    store.upsert_prices(conn, "UPPP", wave_uptrend(320, period=15))
    store.upsert_prices(conn, "FLAT", frame([1000.0] * 320))
    out = backtest.run(cfg, tickers=["UPPP", "FLAT"], workers=1)
    sigs = out["results"]["signals"]
    assert sigs and all(s["ticker"] == "UPPP" for s in sigs)
    last_allowed = wave_uptrend(320).index[-cfg.backtest.horizon_bars - 1]
    assert max(pd.Timestamp(s["date"]) for s in sigs) <= last_allowed
    assert store.latest_backtest(conn)["results"]["overall"]["n"] == len(sigs)
