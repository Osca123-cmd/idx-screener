import math

import numpy as np
import pandas as pd
import pytest

from app import indicators as ind
from app.ticks import round_to_tick, tick_size
from tests.helpers import frame


@pytest.mark.parametrize("price,tick", [(50, 1), (199, 1), (200, 2), (499, 2), (500, 5), (1995, 5), (2000, 10), (4990, 10), (5000, 25), (9875, 25)])
def test_tick_size_bands(price, tick):
    assert tick_size(price) == tick


def test_round_to_tick_modes():
    assert round_to_tick(1237) == 1235
    assert round_to_tick(1238) == 1240
    assert round_to_tick(1238, "down") == 1235
    assert round_to_tick(1236, "up") == 1240
    assert round_to_tick(9862) == 9850
    assert round_to_tick(201) in (200, 202)
    assert round_to_tick(1240, "down") == 1240


def test_ema_constant_series_is_constant():
    s = pd.Series([100.0] * 50)
    assert ind.ema(s, 20).iloc[-1] == pytest.approx(100.0)


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0)
    assert math.isnan(ind.rsi(up).iloc[0])


def test_rsi_alternating_is_near_50():
    s = pd.Series([100.0, 101.0] * 50)
    assert 45 < ind.rsi(s).iloc[-1] < 55


def test_atr_constant_range():
    df = pd.DataFrame({"open": [100.0] * 40, "high": [105.0] * 40, "low": [95.0] * 40, "close": [100.0] * 40, "volume": [1.0] * 40})
    assert ind.atr(df).iloc[-1] == pytest.approx(10.0)


def test_obv_accumulates_by_direction():
    df = frame([10, 11, 11, 10, 12], volume=[100, 200, 300, 400, 500])
    assert list(ind.obv(df)) == [0, 200, 200, -200, 300]


def test_add_all_columns():
    out = ind.add_all(frame(np.linspace(100, 200, 250)))
    for col in ["ema20", "ema50", "ema200", "rsi14", "macd_hist", "atr14", "vol_avg20", "value_avg20", "obv"]:
        assert col in out
    assert out["ema20"].iloc[-1] > out["ema50"].iloc[-1] > out["ema200"].iloc[-1]
