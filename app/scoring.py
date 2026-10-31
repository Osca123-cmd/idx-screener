"""Liquidity filter + 0-100 swing checklist score for one ticker."""
from __future__ import annotations

import math

import pandas as pd

from app import setup as setup_mod
from app.structure import analyze_structure


def passes_filters(df: pd.DataFrame, cfg) -> tuple[bool, str | None]:
    if len(df) < cfg.history.min_bars:
        return False, f"histori {len(df)} bar < {cfg.history.min_bars}"
    last = df.iloc[-1]
    if last["close"] < cfg.filters.min_price:
        return False, f"harga {last['close']:.0f} < {cfg.filters.min_price}"
    value = last["value_avg20"]
    if math.isnan(value) or value < cfg.filters.min_avg_value_idr:
        return False, f"nilai transaksi rata-rata Rp {value / 1e9:.1f} M < Rp {cfg.filters.min_avg_value_idr / 1e9:.0f} M"
    return True, None


def _item(key: str, label: str, max_pts: int, pts: int, detail: str) -> dict:
    return {"key": key, "label": label, "max": max_pts, "points": pts, "ok": pts > 0, "detail": detail}


def analyze(df: pd.DataFrame, cfg) -> dict:
    """df must already have indicator columns (indicators.add_all)."""
    sc = cfg.scoring
    last, prev = df.iloc[-1], df.iloc[-2]
    struct = analyze_structure(df, cfg)
    trend = struct["trend"]
    checks = trend["checks"]
    flags: list[str] = []
    items: list[dict] = []

    # Trend 30
    items.append(_item("above_ema200", "Close di atas EMA200", 10, 10 if checks["above_ema200"] else 0,
                       f"close {last['close']:.0f} vs EMA200 {last['ema200']:.0f}"))
    items.append(_item("ema_stack", "EMA20 > EMA50 > EMA200", 10, 10 if checks["ema_stack_up"] else 0,
                       f"{last['ema20']:.0f} / {last['ema50']:.0f} / {last['ema200']:.0f}"))
    items.append(_item("hh_hl", "Struktur higher-high & higher-low", 10, 10 if checks["hh_hl"] else 0,
                       _swing_text(struct)))

    # Momentum 20
    rsi = float(last["rsi14"])
    if sc.rsi_low <= rsi <= sc.rsi_high:
        rsi_pts = 15
    elif rsi > sc.rsi_high:
        rsi_pts = 5
        flags.append("overbought")
    else:
        rsi_pts = 0
    items.append(_item("rsi", f"RSI14 di {sc.rsi_low}–{sc.rsi_high}", 15, rsi_pts, f"RSI {rsi:.1f}"))
    macd_up = last["macd_hist"] > prev["macd_hist"]
    items.append(_item("macd", "Histogram MACD naik", 5, 5 if macd_up else 0,
                       f"{prev['macd_hist']:.2f} → {last['macd_hist']:.2f}"))

    # Volume 20
    vol_ref = df["volume"].iloc[-21:-1].mean()
    vol_mult = last["volume"] / vol_ref if vol_ref > 0 else 0
    spike = vol_mult >= sc.volume_spike_mult and last["close"] > last["open"]
    items.append(_item("volume", f"Volume ≥ {sc.volume_spike_mult:g}× rata-rata di candle hijau", 15, 15 if spike else 0,
                       f"{vol_mult:.1f}× rata-rata 20 hari"))
    obv_up = last["obv"] > df["obv"].iloc[-1 - sc.obv_lookback]
    items.append(_item("obv", f"OBV naik {sc.obv_lookback} hari", 5, 5 if obv_up else 0, "akumulasi" if obv_up else "distribusi"))

    # Trigger 20 + Room 10
    setup = setup_mod.find_setup(df, struct, cfg)
    triggered = bool(setup and setup["triggered"])
    items.append(_item("trigger", "Trigger: breakout atau pullback ke demand zone", 20, 20 if triggered else 0,
                       setup["detail"] if setup else "tidak ada setup"))
    rr = setup["plan"]["rr"] if setup else 0
    room = 10 if rr >= sc.rr_full else (5 if rr >= sc.rr_half else 0)
    items.append(_item("room", f"Ruang naik (R:R ke TP1 ≥ {sc.rr_full:g})", 10, room, f"R:R {rr:.2f}" if setup else "-"))
    if setup:
        flags += setup["plan"]["flags"]
        if trend["verdict"] == "TURUN":
            flags.append("counter_trend")

    score = sum(i["points"] for i in items)
    return {
        "close": float(last["close"]),
        "change_pct": round((last["close"] / prev["close"] - 1) * 100, 2),
        "score": int(score),
        "trend": trend["verdict"],
        "setup": setup,
        "checklist": items,
        "reasons": [f"{i['label']} ({i['detail']})" for i in items if i["ok"]],
        "flags": flags,
        "struct": struct,
    }


def _swing_text(struct: dict) -> str:
    h, lo = struct["highs"][-2:], struct["lows"][-2:]
    if len(h) < 2 or len(lo) < 2:
        return "swing belum cukup"
    return f"high {h[0].price:.0f}→{h[1].price:.0f}, low {lo[0].price:.0f}→{lo[1].price:.0f}"
