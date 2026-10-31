import numpy as np
import pandas as pd
import pytest

from app import config
from app import indicators as ind
from app import scoring
from app import setup as setup_mod
from app.structure import Swing
from tests.helpers import frame, wave_downtrend, wave_uptrend


@pytest.fixture(scope="module")
def cfg():
    return config.load()


def flat_df(atr_value=20.0, n=60, price=1000.0):
    df = frame([price] * n, spread=0)  # 1-year high == price
    df = ind.add_all(df)
    df["atr14"] = atr_value
    return df


def test_wide_zone_entry_deep_with_scale_in(cfg):
    df = flat_df(atr_value=20)
    plan = setup_mod.build_plan(df, [Swing(10, "x", 1200.0)], 900, 1000, cfg)  # width 100 = 5 ATR
    assert plan["entry"] == 935          # 65% deep: 1000 - 65
    assert plan["sl"] == 890             # 900 - 0.5 ATR
    assert [s["weight"] for s in plan["scale_in"]] == [0.3, 0.4, 0.3]
    assert "wide_zone_scale_in" in plan["flags"]
    assert plan["tp1"] == 1000.0 and plan["tp1_kind"] == "year_high"   # nearest real obstacle
    assert plan["tp2"] == 1200.0


def test_narrow_zone_wider_sl_and_flag(cfg):
    df = flat_df(atr_value=20)
    plan = setup_mod.build_plan(df, [], 990, 1000, cfg)   # width 10 < 1 ATR
    assert plan["entry"] == 995
    assert plan["sl"] == 970                               # 990 - 1 ATR
    assert "narrow_zone_stophunt" in plan["flags"]


def test_room_tight_flag_when_tp1_below_1r(cfg):
    df = flat_df(atr_value=40)
    plan = setup_mod.build_plan(df, [Swing(10, "x", 1030.0)], 950, 1000, cfg)
    assert plan["tp1"] == 1000 and plan["tp2"] == 1030 and plan["rr"] < 1
    assert "room_tight" in plan["flags"]


def test_prices_on_valid_ticks(cfg):
    df = flat_df(atr_value=37.3, price=4300)
    plan = setup_mod.build_plan(df, [], 4123, 4311, cfg)
    for k in ("entry", "sl", "tp1", "tp2"):
        assert plan[k] % 10 == 0, k


def breakout_df():
    up = wave_uptrend(n=300)
    last = up.iloc[-1]
    level = up["high"].iloc[-20:].max()
    c = level * 1.03
    bar = pd.DataFrame({"open": [last["close"]], "high": [c * 1.005], "low": [last["close"] * 0.995], "close": [c],
                        "volume": [3_000_000.0]}, index=[up.index[-1] + pd.offsets.BDay()])
    return ind.add_all(pd.concat([up, bar]))


def test_breakout_scores_high(cfg):
    res = scoring.analyze(breakout_df(), cfg)
    pts = {i["key"]: i["points"] for i in res["checklist"]}
    assert res["trend"] == "NAIK"
    assert res["setup"]["type"] == setup_mod.BREAKOUT and res["setup"]["triggered"]
    assert pts["above_ema200"] == pts["ema_stack"] == 10
    assert pts["volume"] == 15 and pts["trigger"] == 20
    assert res["score"] >= 70
    assert len(res["reasons"]) == sum(1 for i in res["checklist"] if i["ok"])


def test_downtrend_scores_low(cfg):
    res = scoring.analyze(ind.add_all(wave_downtrend()), cfg)
    assert res["trend"] == "TURUN"
    assert res["score"] <= 30


def test_filters(cfg):
    df = ind.add_all(frame(np.linspace(1000, 1100, 250), volume=10_000_000))
    assert scoring.passes_filters(df, cfg) == (True, None)
    thin = ind.add_all(frame(np.linspace(1000, 1100, 250), volume=1000))
    ok, why = scoring.passes_filters(thin, cfg)
    assert not ok and "nilai transaksi" in why
    short = ind.add_all(frame(np.linspace(1000, 1100, 50), volume=10_000_000))
    assert scoring.passes_filters(short, cfg)[0] is False
    cheap = ind.add_all(frame(np.full(250, 40.0), volume=1e9))
    assert "harga" in scoring.passes_filters(cheap, cfg)[1]
