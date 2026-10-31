"""Trade setup: which trigger fired, and the entry / SL / staged TP plan."""
from __future__ import annotations

import pandas as pd

from app.structure import Swing, resistance_levels
from app.ticks import round_to_tick

BREAKOUT = "Breakout"
PULLBACK = "Pullback"
WAITING = "Menunggu pullback"


def build_plan(df: pd.DataFrame, highs: list[Swing], zone_low: float, zone_high: float, cfg) -> dict | None:
    s = cfg.setup
    atr = float(df["atr14"].iloc[-1])
    width = zone_high - zone_low
    flags: list[str] = []
    scale_in = []

    if width > s.wide_zone_atr * atr:
        entry = zone_high - s.entry_depth * width
        depths = [0.25, 0.55, 0.85]
        scale_in = [{"price": round_to_tick(zone_high - d * width), "weight": w} for d, w in zip(depths, s.scale_in)]
        flags.append("wide_zone_scale_in")
    else:
        entry = (zone_high + zone_low) / 2

    if width < s.narrow_zone_atr * atr:
        sl = zone_low - s.sl_buffer_narrow_atr * atr
        flags.append("narrow_zone_stophunt")
    else:
        sl = zone_low - s.sl_buffer_atr * atr

    entry = round_to_tick(entry)
    sl = round_to_tick(sl, "down")
    risk = entry - sl
    if risk <= 0:
        return None

    levels = resistance_levels(df, highs, above=entry + 0.3 * risk)
    if levels:
        tp1, tp1_kind = levels[0].price, levels[0].kind
    else:
        tp1, tp1_kind = entry + cfg.scoring.rr_full * risk, "rr"
        flags.append("no_resistance_above")
    tp1 = round_to_tick(tp1, "down")

    nxt = [lv for lv in levels[1:] if lv.price > tp1 * 1.01]
    if nxt:
        tp2, tp2_kind = nxt[0].price, nxt[0].kind
    else:
        tp2, tp2_kind = max(entry + s.tp2_rr * risk, tp1 + 0.5 * risk), "rr"
    tp2 = round_to_tick(tp2, "down")

    rr = (tp1 - entry) / risk
    if rr < cfg.scoring.rr_half:
        flags.append("room_tight")
    return {
        "zone_low": round_to_tick(zone_low, "down"), "zone_high": round_to_tick(zone_high, "up"),
        "entry": entry, "sl": sl, "tp1": tp1, "tp1_kind": tp1_kind, "tp2": tp2, "tp2_kind": tp2_kind,
        "rr": round(rr, 2), "risk_pct": round(risk / entry * 100, 2), "scale_in": scale_in, "flags": flags,
    }


def find_setup(df: pd.DataFrame, struct: dict, cfg) -> dict | None:
    """Returns {'type', 'triggered', 'detail', 'plan', 'zone'} or None when nothing tradable is nearby."""
    close = float(df["close"].iloc[-1])
    checks = struct["trend"]["checks"]
    uptrend = checks["above_ema200"] and checks["ema_stack_up"]
    valid = [z for z in struct["zones"] if z.valid]
    highs = struct["highs"]
    atr = float(df["atr14"].iloc[-1])

    if uptrend:
        recent_low = df["low"].iloc[-3:]
        for z in valid:
            if (recent_low <= z.high).any() and close >= z.low:
                plan = build_plan(df, highs, z.low, z.high, cfg)
                if plan:
                    plan["flags"] += [f"retest_{z.retests}"] if z.retests > 1 else []
                    return {"type": PULLBACK, "triggered": True, "zone": z.to_dict(), "plan": plan,
                            "detail": f"Harga masuk demand zone {z.low:.0f}–{z.high:.0f} (terbentuk {z.formed_date})"}

    lb = cfg.scoring.breakout_lookback
    if len(df) > lb + 1:
        level = float(df["high"].iloc[-lb - 1:-1].max())
        if close > level:
            plan = build_plan(df, highs, level - cfg.setup.breakout_zone_atr * atr, level, cfg)
            if plan:
                return {"type": BREAKOUT, "triggered": True, "zone": None, "plan": plan,
                        "detail": f"Close {close:.0f} menembus high {lb} hari {level:.0f}; entry di area retest"}

    below = [z for z in valid if z.high < close]
    if below:
        z = below[0]
        plan = build_plan(df, highs, z.low, z.high, cfg)
        if plan:
            return {"type": WAITING, "triggered": False, "zone": z.to_dict(), "plan": plan,
                    "detail": f"Demand zone terdekat {z.low:.0f}–{z.high:.0f}; tunggu harga turun ke zona"}
    return None
