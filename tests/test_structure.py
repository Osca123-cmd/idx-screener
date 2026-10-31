import pandas as pd

from app import indicators as ind
from app import structure as st
from tests.helpers import wave_downtrend, wave_uptrend


def bars(rows, start="2025-01-01"):
    """rows of (open, high, low, close)."""
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1_000_000.0
    return ind.add_all(df)


def sideways(n=40):
    rows, prev = [], 1000.0
    for i in range(n):
        c = 1010.0 if i % 2 else 1000.0
        rows.append((prev, max(prev, c) + 5, min(prev, c) - 5, c))
        prev = c
    return rows


def demand_scenario():
    rows = sideways()
    rows += [
        (1000, 1045, 1000, 1040),  # 40 swing high 1045
        (1040, 1042, 1015, 1020),  # 41
        (1020, 1022, 985, 990),    # 42 bearish base candle
        (990, 1000, 980, 995),     # 43 lowest low 980
        (995, 1065, 990, 1060),    # 44 impulse breaks 1045
        (1060, 1080, 1055, 1075),  # 45
        (1075, 1090, 1070, 1085),  # 46
        (1085, 1088, 1050, 1055),  # 47
        (1055, 1058, 1025, 1030),  # 48
        (1030, 1035, 1012, 1025),  # 49 dips into zone (high 1020)
    ]
    return rows


def test_swings_are_confirmed_only():
    df = bars(demand_scenario())
    highs, lows = st.swing_points(df, 2)
    assert any(s.price == 1045 for s in highs)
    assert all(s.pos <= len(df) - 3 for s in highs + lows)


def test_trend_verdicts():
    up = ind.add_all(wave_uptrend())
    h, lo = st.swing_points(up)
    assert st.trend_verdict(up, h, lo)["verdict"] == "NAIK"
    dn = ind.add_all(wave_downtrend())
    h, lo = st.swing_points(dn)
    assert st.trend_verdict(dn, h, lo)["verdict"] == "TURUN"


def test_demand_zone_found_with_retest():
    df = bars(demand_scenario())
    highs, _ = st.swing_points(df, 2)
    zones = st.demand_zones(df, highs, 2, 2.0, 120)
    z = zones[0]
    assert (z.low, z.high) == (980, 1020)
    assert z.break_level == 1045 and z.valid
    assert z.retests == 1


def test_demand_zone_invalidated_by_close_below():
    rows = demand_scenario() + [(1025, 1026, 960, 970)]
    df = bars(rows)
    highs, _ = st.swing_points(df, 2)
    zones = [z for z in st.demand_zones(df, highs, 2, 2.0, 120) if z.low == 980]
    assert zones and not zones[0].valid


def test_resistance_levels_nearest_first_and_round():
    df = bars(demand_scenario())
    highs, _ = st.swing_points(df, 2)
    lv = st.resistance_levels(df, highs, above=1030)
    prices = [round(x.price) for x in lv]
    assert prices == sorted(prices)
    assert 1090 in prices            # swing high / year high at 1090
    assert not any(x.kind == "round" for x in lv)  # 1100 is within 1.5% of 1090 -> merged away
    above_all = st.resistance_levels(df, highs, above=1095)
    assert [(x.kind, x.price) for x in above_all] == [("round", 1100)]
