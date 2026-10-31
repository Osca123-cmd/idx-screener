"""Relative valuation labels: PER/PBV vs sector median (and own history once it exists)."""
from __future__ import annotations

import math
import statistics

from app.universe import Stock

UNDER, FAIR, OVER = "Undervalue", "Wajar", "Overvalue"
LOSS = "N/A (rugi)"
NO_DATA = "Data tidak tersedia"
MIN_PEERS = 5


def ratios(f: dict | None, close: float) -> tuple[float | None, float | None]:
    """PER/PBV recomputed against our own last close so they match the screener's price."""
    if not f:
        return None, None
    eps, bvps = f.get("eps"), f.get("bvps")
    per = close / eps if eps and eps > 0 else None
    pbv = close / bvps if bvps and bvps > 0 else None
    return per, pbv


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def sector_medians(stocks: list[Stock], funds: dict[str, dict], closes: dict[str, float], per_max: float) -> dict:
    """{'sectors': {sector: {'per', 'pbv', 'n'}}, 'market': {...}}"""
    by_sector: dict[str, dict[str, list[float]]] = {}
    market = {"per": [], "pbv": []}
    for s in stocks:
        if s.ticker not in closes:
            continue
        per, pbv = ratios(funds.get(s.ticker), closes[s.ticker])
        bucket = by_sector.setdefault(s.sector, {"per": [], "pbv": []})
        if per is not None and 0 < per <= per_max:
            bucket["per"].append(per)
            market["per"].append(per)
        if pbv is not None and 0 < pbv <= 50:
            bucket["pbv"].append(pbv)
            market["pbv"].append(pbv)
    out = {
        sec: {"per": _median(v["per"]), "pbv": _median(v["pbv"]), "n": max(len(v["per"]), len(v["pbv"]))}
        for sec, v in by_sector.items()
    }
    return {"sectors": out, "market": {"per": _median(market["per"]), "pbv": _median(market["pbv"])}}


def percentile(value: float, history: list[float]) -> float:
    below = sum(1 for h in history if h < value)
    return below / len(history) * 100


def graham(f: dict | None, close: float) -> tuple[float | None, float | None]:
    if not f:
        return None, None
    eps, bvps = f.get("eps"), f.get("bvps")
    if not (eps and bvps and eps > 0 and bvps > 0):
        return None, None
    g = math.sqrt(22.5 * eps * bvps)
    return g, (g - close) / g * 100


def label(stock: Stock, f: dict | None, close: float, medians: dict, cfg, history: list[dict] | None = None) -> dict:
    v = cfg.valuation
    per, pbv = ratios(f, close)
    g, mos = graham(f, close)
    base = {"per": per, "pbv": pbv, "graham": g, "graham_mos": mos, "history_note": None}

    if not f or (f.get("eps") is None and f.get("bvps") is None):
        return {**base, "label": NO_DATA, "reason": "Fundamental tidak tersedia dari sumber data", "ratio": None}
    if f.get("eps") is not None and f["eps"] <= 0:
        reason = f"EPS {f['eps']:.1f} (rugi)"
        if pbv is not None:
            reason += f" · PBV {pbv:.2f}"
        return {**base, "label": LOSS, "reason": reason, "ratio": None}

    sec = medians["sectors"].get(stock.sector, {})
    ref, ref_name = sec, f"median sektor {stock.sector}"
    if sec.get("n", 0) < MIN_PEERS:
        ref, ref_name = medians["market"], "median pasar (sektor < 5 emiten)"

    parts, reasons = [], []
    if not stock.is_financial and per is not None and ref.get("per"):
        parts.append(per / ref["per"])
        reasons.append(f"PER {per:.1f} vs {ref['per']:.1f}")
    if pbv is not None and ref.get("pbv"):
        parts.append(pbv / ref["pbv"])
        reasons.append(f"PBV {pbv:.2f} vs {ref['pbv']:.2f}")
    if not parts:
        return {**base, "label": NO_DATA, "reason": "PER/PBV tidak bisa dihitung", "ratio": None}

    ratio = sum(parts) / len(parts)
    lab = UNDER if ratio < v.under else (OVER if ratio > v.over else FAIR)
    reason = " · ".join(reasons) + f" ({ref_name}{', bank: PBV saja' if stock.is_financial else ''})"

    if history and len(history) >= v.history_min_snapshots:
        key, cur = ("pbv", pbv) if stock.is_financial or per is None else ("per", per)
        hist = [h[key] for h in history if h.get(key)]
        if cur is not None and len(hist) >= v.history_min_snapshots:
            pct = percentile(cur, hist)
            if pct < v.history_low_pct:
                base["history_note"] = f"murah vs histori sendiri (persentil {pct:.0f})"
            elif pct > v.history_high_pct:
                base["history_note"] = f"mahal vs histori sendiri (persentil {pct:.0f})"
            else:
                base["history_note"] = f"wajar vs histori sendiri (persentil {pct:.0f})"
    return {**base, "label": lab, "reason": reason, "ratio": round(ratio, 2)}
