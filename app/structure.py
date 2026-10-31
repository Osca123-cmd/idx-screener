"""Market structure: swing points, trend verdict, demand zones, resistance levels.

Everything here only looks at bars up to the last row of the frame it is given,
so the backtest can call it on truncated frames without look-ahead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Swing:
    pos: int
    date: str
    price: float


@dataclass
class Zone:
    low: float
    high: float
    formed_pos: int
    formed_date: str
    base_date: str
    break_level: float
    retests: int
    valid: bool

    @property
    def width(self) -> float:
        return self.high - self.low

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Level:
    price: float
    kind: str       # swing_high | cluster | round | year_high
    touches: int = 1


def _date(df: pd.DataFrame, i: int) -> str:
    return df.index[i].strftime("%Y-%m-%d")


def swing_points(df: pd.DataFrame, n: int = 2) -> tuple[list[Swing], list[Swing]]:
    """Fractal swings, only those already confirmed by n bars on the right."""
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    highs, lows = [], []
    for i in range(n, len(df) - n):
        win_h = h[i - n:i + n + 1]
        win_l = lo[i - n:i + n + 1]
        if h[i] == win_h.max() and (win_h[:n] < h[i]).all():
            highs.append(Swing(i, _date(df, i), float(h[i])))
        if lo[i] == win_l.min() and (win_l[:n] > lo[i]).all():
            lows.append(Swing(i, _date(df, i), float(lo[i])))
    return highs, lows


def trend_verdict(df: pd.DataFrame, highs: list[Swing], lows: list[Swing]) -> dict:
    last = df.iloc[-1]
    hh_hl = len(highs) >= 2 and len(lows) >= 2 and highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
    lh_ll = len(highs) >= 2 and len(lows) >= 2 and highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
    checks = {
        "above_ema200": bool(last["close"] > last["ema200"]),
        "ema_stack_up": bool(last["ema20"] > last["ema50"] > last["ema200"]),
        "hh_hl": bool(hh_hl),
        "below_ema200": bool(last["close"] < last["ema200"]),
        "ema_stack_down": bool(last["ema20"] < last["ema50"] < last["ema200"]),
        "lh_ll": bool(lh_ll),
    }
    if checks["above_ema200"] and checks["ema_stack_up"] and checks["hh_hl"]:
        verdict = "NAIK"
    elif checks["below_ema200"] and checks["ema_stack_down"] and checks["lh_ll"]:
        verdict = "TURUN"
    else:
        verdict = "SIDEWAYS"
    return {"verdict": verdict, "checks": checks}


def _count_retests(low: np.ndarray, zone_high: float, start: int) -> int:
    """Separate visits into the zone after it formed (a visit = bar low <= zone high after being above it)."""
    count, inside = 0, True  # the impulse bar itself starts "inside"
    for i in range(start, len(low)):
        touching = low[i] <= zone_high
        if touching and not inside:
            count += 1
        inside = touching
    return count


def demand_zones(df: pd.DataFrame, highs: list[Swing], n: int = 2, impulse_atr_mult: float = 2.0,
                 lookback: int = 120) -> list[Zone]:
    """Base of an impulsive up-leg that broke a prior swing high. Most recent first."""
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c = df["close"].to_numpy()
    atr = df["atr14"].to_numpy()
    last = len(df) - 1
    first = max(1, last - lookback)
    zones: list[Zone] = []
    used_swings: set[int] = set()

    for b in range(first, last + 1):
        # most recent swing high that was confirmed before bar b
        prior = [s for s in highs if s.pos + n < b]
        if not prior:
            continue
        sh = prior[-1]
        if sh.pos in used_swings or not (c[b] > sh.price >= c[b - 1]):
            continue
        base_pos = sh.pos + int(np.argmin(lo[sh.pos:b + 1]))
        impulse = h[base_pos:b + 1].max() - lo[base_pos]
        if np.isnan(atr[b]) or impulse < impulse_atr_mult * atr[b]:
            continue
        # last bearish candle at or just before the low = the base candle
        base_candle = base_pos
        for k in range(base_pos, max(sh.pos, base_pos - 3) - 1, -1):
            if c[k] < o[k]:
                base_candle = k
                break
        z_low = float(lo[min(base_candle, base_pos):base_pos + 1].min())
        z_high = float(max(o[base_candle], c[base_candle]))
        if z_high <= z_low:
            z_high = float(h[base_candle])
        valid = bool((c[b + 1:] >= z_low).all())
        zones.append(Zone(
            low=z_low, high=z_high, formed_pos=b, formed_date=_date(df, b),
            base_date=_date(df, min(base_candle, base_pos)), break_level=sh.price,
            retests=_count_retests(lo, z_high, b + 1), valid=valid,
        ))
        used_swings.add(sh.pos)

    zones.sort(key=lambda z: z.formed_pos, reverse=True)
    # drop zones that overlap a more recent one
    kept: list[Zone] = []
    for z in zones:
        if all(z.high < k.low or z.low > k.high for k in kept):
            kept.append(z)
    return kept


def round_step(price: float) -> float:
    if price < 500:
        return 50
    if price < 2000:
        return 100
    if price < 5000:
        return 250
    if price < 10000:
        return 500
    return 1000


def resistance_levels(df: pd.DataFrame, highs: list[Swing], above: float, lookback: int = 250,
                      cluster_pct: float = 0.015) -> list[Level]:
    """Real obstacles above `above`, nearest first: swing highs (clustered), 1-year high, next round number."""
    start = max(0, len(df) - lookback)
    candidates = sorted(s.price for s in highs if s.pos >= start and s.price > above)
    levels: list[Level] = []
    for p in candidates:
        if levels and p <= levels[-1].price * (1 + cluster_pct):
            lv = levels[-1]
            lv.price = (lv.price * lv.touches + p) / (lv.touches + 1)
            lv.touches += 1
            lv.kind = "cluster"
        else:
            levels.append(Level(p, "swing_high"))
    year_high = float(df["high"].iloc[start:].max())
    if year_high > above and all(abs(year_high - lv.price) / lv.price > cluster_pct for lv in levels):
        levels.append(Level(year_high, "year_high"))
    step = round_step(above)
    rnd = (np.floor(above / step) + 1) * step
    if all(abs(rnd - lv.price) / lv.price > cluster_pct for lv in levels):
        levels.append(Level(float(rnd), "round"))
    levels.sort(key=lambda lv: lv.price)
    return levels


def analyze_structure(df: pd.DataFrame, cfg) -> dict:
    """Minor swings (small fractal) mark the breaks that create demand zones;
    major swings decide the trend and the resistance levels used for TPs."""
    s = cfg.structure
    minor_highs, _ = swing_points(df, s.fractal)
    highs, lows = swing_points(df, s.major_fractal)
    return {
        "highs": highs,
        "lows": lows,
        "trend": trend_verdict(df, highs, lows),
        "zones": demand_zones(df, minor_highs, s.fractal, s.impulse_atr_mult, s.zone_lookback),
    }
