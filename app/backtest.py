"""Replay the scoring/setup engine over history and measure how its triggered setups played out.

At bar t only bars <= t are given to the engine (indicators are causal, structure is recomputed
on the truncated frame), so there is no look-ahead. Each signal is then simulated on the
following `horizon` bars as a limit order at the entry price:
  - not filled within the horizon      -> no_fill
  - SL before TP1 (same bar -> SL)      -> loss, -1R
  - TP1 before SL                       -> win, +rr R
  - neither by the end of the horizon   -> timeout, mark-to-market R
Filled trades then pay round-trip fees, expressed in R (fee_pct / risk_pct).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from app import config, indicators, scoring, store

BUCKETS = [(0, 49, "0–49"), (50, 69, "50–69"), (70, 100, "70–100")]


def simulate(plan: dict, future: pd.DataFrame, fee_pct: float = 0.0) -> dict:
    entry, sl, tp1 = plan["entry"], plan["sl"], plan["tp1"]
    risk = entry - sl
    fee_r = fee_pct / 100 * entry / risk

    def done(outcome, r, bars):
        return {"outcome": outcome, "r": round(r - fee_r, 3), "bars": bars}

    filled_at = None
    for i, (_, b) in enumerate(future.iterrows()):
        if filled_at is None:
            if b["low"] > entry:
                continue
            filled_at = i
        if b["low"] <= sl:
            return done("loss", -1.0, i + 1)
        if b["high"] >= tp1:
            return done("win", (tp1 - entry) / risk, i + 1)
    if filled_at is None:
        return {"outcome": "no_fill", "r": 0.0, "bars": len(future)}
    return done("timeout", (float(future["close"].iloc[-1]) - entry) / risk, len(future))


def candidate_mask(dfi: pd.DataFrame, lookback: int) -> pd.Series:
    """Bars where a trigger is possible at all (exact superset of find_setup's triggers)."""
    uptrend = (dfi["close"] > dfi["ema200"]) & (dfi["ema20"] > dfi["ema50"]) & (dfi["ema50"] > dfi["ema200"])
    breakout = dfi["close"] > dfi["high"].shift(1).rolling(lookback).max()
    return uptrend | breakout


def signals_for(ticker: str, prices: pd.DataFrame, cfg) -> list[dict]:
    b = cfg.backtest
    if len(prices) < b.warmup_bars + b.horizon_bars + 1:
        return []
    dfi = indicators.add_all(prices)
    mask = candidate_mask(dfi, cfg.scoring.breakout_lookback).to_numpy()
    out: list[dict] = []
    t = b.warmup_bars
    last_t = len(dfi) - b.horizon_bars - 1
    while t <= last_t:
        if not mask[t]:
            t += b.step
            continue
        window = dfi.iloc[:t + 1]
        ok, _ = scoring.passes_filters(window, cfg)
        res = scoring.analyze(window, cfg) if ok else None
        setup = res and res["setup"]
        if not (setup and setup["triggered"]):
            t += b.step
            continue
        sim = simulate(setup["plan"], dfi.iloc[t + 1:t + 1 + b.horizon_bars], b.fee_pct)
        out.append({
            "ticker": ticker, "date": dfi.index[t].strftime("%Y-%m-%d"), "setup": setup["type"],
            "score": res["score"], "trend": res["trend"], "rr": setup["plan"]["rr"], **sim,
        })
        t += max(sim["bars"], b.step)  # no overlapping signals while a trade is open / pending
    return out


def _worker(args):
    ticker, prices, cfg_path = args
    cfg = config.load(cfg_path) if cfg_path else config.get()
    return signals_for(ticker, prices, cfg)


def aggregate(signals: list[dict]) -> dict:
    def stats(rows: list[dict]) -> dict:
        filled = [r for r in rows if r["outcome"] != "no_fill"]
        wins = [r for r in filled if r["outcome"] == "win"]
        return {
            "n": len(rows),
            "filled": len(filled),
            "fill_rate": round(len(filled) / len(rows) * 100, 1) if rows else None,
            "win_rate": round(len(wins) / len(filled) * 100, 1) if filled else None,
            "avg_r": round(sum(r["r"] for r in filled) / len(filled), 3) if filled else None,
            "losses": sum(1 for r in filled if r["outcome"] == "loss"),
            "timeouts": sum(1 for r in filled if r["outcome"] == "timeout"),
        }

    by_setup = {s: stats([r for r in signals if r["setup"] == s]) for s in sorted({r["setup"] for r in signals})}
    by_score = {label: stats([r for r in signals if lo <= r["score"] <= hi]) for lo, hi, label in BUCKETS}
    by_trend = {t: stats([r for r in signals if r["trend"] == t]) for t in sorted({r["trend"] for r in signals})}
    return {"overall": stats(signals), "by_setup": by_setup, "by_score": by_score, "by_trend": by_trend}


def run(cfg=None, tickers: list[str] | None = None, workers: int | None = None, save: bool = True) -> dict:
    cfg = cfg or config.get()
    conn = store.connect(cfg.database)
    try:
        if not tickers:
            scan = store.latest_scan(conn)
            tickers = [r["ticker"] for r in store.scan_results(conn, scan["id"])] if scan else []
        jobs = [(t, store.load_prices(conn, t), None) for t in tickers]
        workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
        signals: list[dict] = []
        if workers == 1:
            for t, prices, _ in jobs:
                signals += signals_for(t, prices, cfg)
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for rows in pool.map(_worker, jobs, chunksize=4):
                    signals += rows
        results = aggregate(signals)
        results["signals"] = sorted(signals, key=lambda r: r["date"], reverse=True)[:500]
        params = {"tickers": len(tickers), "horizon_bars": cfg.backtest.horizon_bars,
                  "warmup_bars": cfg.backtest.warmup_bars, "fee_pct": cfg.backtest.fee_pct}
        if save:
            store.save_backtest(conn, params, results)
        return {"params": params, "results": results}
    finally:
        conn.close()


def summary_text(run_result: dict) -> str:
    res = run_result["results"]
    p = run_result["params"]
    lines = [f"tickers={p['tickers']} horizon={p['horizon_bars']} bars fee={p.get('fee_pct', 0)}%"]

    def fmt(name, s):
        return (f"  {name:12} n={s['n']:>4} fill={s['fill_rate']}% win={s['win_rate']}% "
                f"avgR={s['avg_r']} loss={s['losses']} timeout={s['timeouts']}")

    lines.append(fmt("overall", res["overall"]))
    for group in ("by_setup", "by_score", "by_trend"):
        lines.append(group)
        for k, s in res[group].items():
            lines.append(fmt(k, s))
    return "\n".join(lines)
