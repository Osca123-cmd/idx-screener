"""Jinja filters and display helpers (Indonesian number formatting, flag texts, ladder geometry)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

FLAG_TEXT = {
    "room_tight": ("Ruang naik sempit", "TP1 kurang dari 1R dari entry — hadiahnya kecil dibanding risikonya."),
    "sl_far": ("SL jauh", "Jarak SL lebih dari 8% — kecilkan lot agar risiko per trade tetap sama."),
    "narrow_zone_stophunt": ("Zona sempit", "Rawan stop-hunt; buffer SL sudah diperlebar 1×ATR."),
    "wide_zone_scale_in": ("Zona lebar", "Entry di kedalaman 65%; pertimbangkan masuk bertahap 30/40/30."),
    "counter_trend": ("Melawan trend", "Trend harian TURUN — setup ini probabilitasnya lebih rendah."),
    "value_trap": ("Potensi value trap", "Murah secara valuasi, tapi trend masih turun."),
    "overbought": ("Overbought", "RSI di atas 70 — rawan koreksi dulu."),
    "no_resistance_above": ("Tanpa resistance", "Tidak ada hambatan di atas; TP dihitung dari R."),
}

KIND_TEXT = {"swing_high": "swing high", "cluster": "cluster resistance", "round": "angka bulat",
             "year_high": "high 1 tahun", "rr": "berbasis R"}


def price(v) -> str:
    if v is None:
        return "–"
    return f"{v:,.0f}".replace(",", ".")


def num(v, digits=2) -> str:
    if v is None:
        return "–"
    return f"{v:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def pct(v) -> str:
    if v is None:
        return "–"
    sign = "+" if v > 0 else ""
    return f"{sign}{num(v, 2)}%"


def idr_big(v) -> str:
    if v is None:
        return "–"
    if v >= 1e12:
        return f"Rp {num(v / 1e12, 1)} T"
    if v >= 1e9:
        return f"Rp {num(v / 1e9, 1)} M"
    return f"Rp {price(v)}"


def date_id(s) -> str:
    if not s:
        return "–"
    d = datetime.fromisoformat(str(s)[:19])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def flag(code: str) -> tuple[str, str]:
    if code.startswith("retest_"):
        n = code.split("_")[1]
        return f"Dites {n}×", f"Zona sudah dikunjungi {n} kali — makin sering dites, makin lemah."
    return FLAG_TEXT.get(code, (code, ""))


def ladder(r: dict) -> dict | None:
    """Positions (0-100%) of SL, zone, entry, close, TP1, TP2 on one horizontal scale."""
    if not r or r.get("entry") is None:
        return None
    pts = [r["sl"], r["zone_low"], r["zone_high"], r["entry"], r["tp1"], r["tp2"], r["close"]]
    lo, hi = min(p for p in pts if p), max(p for p in pts if p)
    span = (hi - lo) or 1

    def x(v):
        return round((v - lo) / span * 100, 2)

    return {k: x(r[k]) for k in ("sl", "zone_low", "zone_high", "entry", "tp1", "tp2", "close")}


STATIC_DIR = Path(__file__).parent / "static"


def static_version() -> int:
    """Cache-busting suffix for static assets; changes whenever a static file changes."""
    return int(max(f.stat().st_mtime for f in STATIC_DIR.rglob("*") if f.is_file()))


def server_links() -> dict:
    """URL helpers for the local FastAPI app."""
    v = static_version()
    pages = {"screener": "/", "watchlist": "/watchlist", "backtest": "/backtest"}

    def page_url(kind: str, ticker: str | None = None) -> str:
        if kind == "stock":
            return f"/stock/{ticker}"
        if kind == "chart":
            return f"/api/chart/{ticker}"
        return pages[kind]

    return {"page_url": page_url, "asset": lambda p: f"/static/{p}?v={v}", "site_mode": False}


def site_links(root: str, stock_pages: set[str]) -> dict:
    """URL helpers for the static GitHub Pages build. root is '' or '../' depending on page depth."""
    v = static_version()
    pages = {"screener": "index.html", "watchlist": "watchlist.html", "backtest": "backtest.html"}

    def page_url(kind: str, ticker: str | None = None) -> str | None:
        if kind == "stock":
            return f"{root}stock/{ticker}.html" if ticker in stock_pages else None
        if kind == "chart":
            return f"{root}data/chart/{ticker}.json"
        return root + pages[kind]

    return {"page_url": page_url, "asset": lambda p: f"{root}static/{p}?v={v}", "site_mode": True}


def register(env) -> None:
    env.filters.update(price=price, num=num, pct=pct, idr_big=idr_big, date_id=date_id)
    env.globals.update(flag=flag, ladder=ladder, KIND_TEXT=KIND_TEXT)
