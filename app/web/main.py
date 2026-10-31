"""FastAPI web UI. Reads SQLite only; scans run in a background thread."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import backtest, config, indicators, jobs, scoring, store, universe, valuation, watch
from app.web import fmt

HERE = Path(__file__).parent
app = FastAPI(title="IDX Screener")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
fmt.register(templates.env)
# cache-busting suffix for /static assets, changes whenever a static file changes
templates.env.globals["static_v"] = int(max(f.stat().st_mtime for f in (HERE / "static").rglob("*") if f.is_file()))

BACKTEST_STATE = {"running": False, "error": None}


def db():
    return store.connect(config.get().database)


def _stocks() -> dict[str, universe.Stock]:
    return universe.by_ticker(universe.load())


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _header(conn) -> dict:
    scan = store.latest_scan(conn)
    last_any = store.latest_scan(conn, status=None)
    return {"scan": scan, "last_attempt": last_any, "progress": jobs.PROGRESS}


# --- screener ---------------------------------------------------------------

SORTS = {
    "score": lambda r: (-(r["score"] or 0), r["ticker"]),
    "rr": lambda r: (-(r["rr"] or -9), r["ticker"]),
    "change": lambda r: (-(r["change_pct"] or 0), r["ticker"]),
    "ticker": lambda r: r["ticker"],
}


def filter_results(rows: list[dict], q: dict) -> list[dict]:
    out = rows
    if q.get("min_score"):
        out = [r for r in out if (r["score"] or 0) >= int(q["min_score"])]
    if q.get("setup"):
        if q["setup"] == "triggered":
            out = [r for r in out if r["setup_type"] in ("Breakout", "Pullback")]
        else:
            out = [r for r in out if r["setup_type"] == q["setup"]]
    if q.get("valuation"):
        out = [r for r in out if r["valuation_label"] == q["valuation"]]
    if q.get("trend"):
        out = [r for r in out if r["trend"] == q["trend"]]
    if q.get("sector"):
        out = [r for r in out if r["detail"].get("sector") == q["sector"]]
    if q.get("q"):
        needle = q["q"].upper()
        out = [r for r in out if needle in r["ticker"] or needle in r["detail"].get("name", "").upper()]
    return sorted(out, key=SORTS.get(q.get("sort") or "score", SORTS["score"]))


@app.get("/", response_class=HTMLResponse)
def screener(request: Request):
    q = dict(request.query_params)
    q.setdefault("min_score", "")
    conn = db()
    try:
        head = _header(conn)
        rows = store.scan_results(conn, head["scan"]["id"]) if head["scan"] else []
        watched = {w["ticker"] for w in store.watch_list(conn)}
    finally:
        conn.close()
    shown = filter_results(rows, q)
    ctx = {"request": request, "rows": shown, "total": len(rows), "q": q, "watched": watched,
           "sectors": sorted({r["detail"].get("sector") for r in rows if r["detail"].get("sector")}), **head}
    if _is_htmx(request) and request.headers.get("HX-Target") == "results":
        return templates.TemplateResponse(request, "partials/results.html", ctx)
    return templates.TemplateResponse(request, "screener.html", ctx)


# --- stock detail -----------------------------------------------------------

def _on_the_fly(conn, ticker: str, stock: universe.Stock) -> tuple[dict | None, str | None]:
    """Analysis for a ticker that is not in the latest scan (e.g. filtered out as illiquid)."""
    cfg = config.get()
    df = store.load_prices(conn, ticker)
    if len(df) < 60:
        return None, "Belum ada cukup data harga untuk saham ini."
    df = jobs.drop_unclosed_bar(df, jobs.market_now(), cfg.market.close_cutoff)
    dfi = indicators.add_all(df)
    ok, why = scoring.passes_filters(dfi, cfg)
    analysis = scoring.analyze(dfi, cfg)
    funds = store.all_fundamentals(conn)
    closes = {}
    for t in funds:
        row = conn.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1", (t,)).fetchone()
        if row:
            closes[t] = row[0]
    medians = valuation.sector_medians(universe.load(), funds, closes, cfg.valuation.per_max)
    val = valuation.label(stock, funds.get(ticker), analysis["close"], medians, cfg)
    result = jobs.build_result(stock, analysis, val)
    return result, (None if ok else f"Tidak masuk screener: {why}. Analisa di bawah dihitung langsung, bukan dari scan.")


@app.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_detail(request: Request, ticker: str):
    ticker = ticker.upper()
    stocks = _stocks()
    if ticker not in stocks:
        raise HTTPException(404, f"{ticker} tidak ada di daftar emiten")
    conn = db()
    try:
        head = _header(conn)
        result = store.ticker_result(conn, head["scan"]["id"], ticker) if head["scan"] else None
        note = None
        if result is None:
            result, note = _on_the_fly(conn, ticker, stocks[ticker])
        history = store.score_history(conn, ticker)
        watched = any(w["ticker"] == ticker for w in store.watch_list(conn))
    finally:
        conn.close()
    return templates.TemplateResponse(request, "stock.html", {
        "request": request, "stock": stocks[ticker], "r": result, "note": note,
        "history": history, "watched": watched, **head,
    })


@app.get("/api/chart/{ticker}")
def chart_data(ticker: str, bars: int = 260):
    ticker = ticker.upper()
    conn = db()
    try:
        df = store.load_prices(conn, ticker)
    finally:
        conn.close()
    if df.empty:
        raise HTTPException(404, "no data")
    dfi = indicators.add_all(df).iloc[-bars:]
    t = [d.strftime("%Y-%m-%d") for d in dfi.index]

    def series(col):
        return [{"time": d, "value": round(float(v), 2)} for d, v in zip(t, dfi[col]) if v == v]

    return JSONResponse({
        "candles": [{"time": d, "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for d, r in zip(t, dfi.itertuples())],
        "volume": [{"time": d, "value": r.volume, "up": r.close >= r.open} for d, r in zip(t, dfi.itertuples())],
        "ema20": series("ema20"), "ema50": series("ema50"), "ema200": series("ema200"),
    })


# --- watchlist --------------------------------------------------------------

@app.post("/watchlist/{ticker}")
def watch_add(request: Request, ticker: str):
    ticker = ticker.upper()
    conn = db()
    try:
        scan = store.latest_scan(conn)
        r = store.ticker_result(conn, scan["id"], ticker) if scan else None
        if r is None or r["entry"] is None:
            raise HTTPException(400, "Saham ini belum punya plan entry di scan terakhir")
        plan = {k: r[k] for k in ("entry", "sl", "tp1", "tp2", "zone_low", "zone_high", "rr", "setup_type", "score")}
        plan["as_of"] = scan["scan_date"]
        store.watch_add(conn, ticker, plan)
    finally:
        conn.close()
    return RedirectResponse(request.headers.get("referer") or "/watchlist", status_code=303)


@app.post("/watchlist/{ticker}/delete")
def watch_delete(request: Request, ticker: str):
    conn = db()
    try:
        store.watch_remove(conn, ticker.upper())
    finally:
        conn.close()
    return RedirectResponse(request.headers.get("referer") or "/watchlist", status_code=303)


@app.get("/watchlist", response_class=HTMLResponse)
def watchlist(request: Request):
    conn = db()
    stocks = _stocks()
    try:
        head = _header(conn)
        items = []
        for w in store.watch_list(conn):
            plan = w["plan"]
            prices = store.load_prices(conn, w["ticker"])
            after = prices[prices.index > plan["as_of"]]
            st = watch.status(plan, after)
            latest = store.ticker_result(conn, head["scan"]["id"], w["ticker"]) if head["scan"] else None
            items.append({
                "ticker": w["ticker"], "stock": stocks.get(w["ticker"]), "plan": plan, "status": st,
                "close": float(prices["close"].iloc[-1]) if len(prices) else None,
                "latest": latest, "added_at": w["added_at"],
            })
    finally:
        conn.close()
    return templates.TemplateResponse(request, "watchlist.html", {"request": request, "items": items, **head})


# --- backtest ---------------------------------------------------------------

@app.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request):
    conn = db()
    try:
        head = _header(conn)
        run = store.latest_backtest(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "backtest.html",
                                      {"request": request, "run": run, "bt": BACKTEST_STATE, **head})


def _run_backtest():
    BACKTEST_STATE.update(running=True, error=None)
    try:
        backtest.run()
    except Exception as e:
        BACKTEST_STATE["error"] = f"{type(e).__name__}: {e}"
    finally:
        BACKTEST_STATE["running"] = False


@app.post("/backtest/run")
def backtest_run():
    if not BACKTEST_STATE["running"]:
        threading.Thread(target=_run_backtest, daemon=True).start()
    return RedirectResponse("/backtest", status_code=303)


# --- scan -------------------------------------------------------------------

def _run_scan():
    try:
        jobs.daily_scan(force=True)
    except jobs.ScanLocked as e:
        jobs.PROGRESS["error"] = str(e)
    except Exception as e:
        jobs.PROGRESS["error"] = f"{type(e).__name__}: {e}"


@app.post("/scan", response_class=HTMLResponse)
def scan_start(request: Request):
    if not jobs.PROGRESS["running"]:
        jobs.PROGRESS.update(running=True, stage="Mulai", done=0, total=0, error=None)
        threading.Thread(target=_run_scan, daemon=True).start()
    return templates.TemplateResponse(request, "partials/scan_status.html", {"request": request, "progress": jobs.PROGRESS})


@app.get("/scan/status", response_class=HTMLResponse)
def scan_status(request: Request):
    resp = templates.TemplateResponse(request, "partials/scan_status.html", {"request": request, "progress": jobs.PROGRESS})
    if not jobs.PROGRESS["running"] and request.query_params.get("polling"):
        resp.headers["HX-Refresh"] = "true"
    return resp
