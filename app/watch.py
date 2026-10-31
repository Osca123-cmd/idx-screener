"""Watchlist status: replay bars since a plan was saved against its entry / SL / TP levels."""
from __future__ import annotations

import pandas as pd

WAITING = "Menunggu entry"
IN_TRADE = "Entry tersentuh"
TP1 = "TP1 kena"
TP2 = "TP2 kena"
SL = "SL kena"
INVALID = "Zona invalid"


def status(plan: dict, bars: pd.DataFrame) -> dict:
    """bars: daily OHLC strictly after the plan was saved.

    When SL and a TP fall inside the same bar we assume SL hit first (conservative).
    After TP1 the stop is treated as moved to breakeven while waiting for TP2.
    """
    entered_on = tp1_on = None
    for date, b in bars.iterrows():
        d = date.strftime("%Y-%m-%d")
        if entered_on is None:
            if b["low"] > plan["entry"]:
                continue
            entered_on = d
        if tp1_on is None:
            if b["low"] <= plan["sl"]:
                return {"status": SL, "date": d, "entered_on": entered_on}
            if b["high"] >= plan["tp1"]:
                tp1_on = d
                if b["high"] >= plan["tp2"]:
                    return {"status": TP2, "date": d, "entered_on": entered_on}
            elif plan.get("zone_low") and b["close"] < plan["zone_low"]:
                # closed below the demand zone without touching SL: the setup is broken, exit
                return {"status": INVALID, "date": d, "entered_on": entered_on}
            continue
        if b["high"] >= plan["tp2"]:
            return {"status": TP2, "date": d, "entered_on": entered_on}
        if b["low"] <= plan["entry"]:
            break  # breakeven stop after TP1
    if tp1_on:
        return {"status": TP1, "date": tp1_on, "entered_on": entered_on}
    if entered_on:
        return {"status": IN_TRADE, "date": entered_on, "entered_on": entered_on}
    return {"status": WAITING, "date": None, "entered_on": None}
