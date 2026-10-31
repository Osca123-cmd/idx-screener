"""Batch jobs: daily scan, fundamentals refresh, backtest. Run as `python -m app.jobs <command>`."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app import config, indicators, scoring, store, universe, valuation
from app.data.tradingview import TradingViewFundamentals
from app.data.yahoo import YahooSource

log = logging.getLogger("jobs")
JAKARTA = ZoneInfo("Asia/Jakarta")

# In-process progress, polled by the web UI while it runs a scan in a background thread.
PROGRESS = {"running": False, "stage": "", "done": 0, "total": 0, "scan_id": None, "error": None}


class ScanLocked(RuntimeError):
    pass


def market_now() -> datetime:
    return datetime.now(JAKARTA)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) would *terminate* the process on Windows, so ask the kernel instead.
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(lock: Path, max_age_hours: float) -> bool:
    if time.time() - lock.stat().st_mtime > max_age_hours * 3600:
        return True
    try:
        return not pid_alive(int(lock.read_text().strip() or 0))
    except (OSError, ValueError):
        return True


@contextmanager
def scan_lock(db_path: str, max_age_hours: float = 2):
    lock = Path(db_path).with_suffix(".lock")
    if lock.exists() and _lock_is_stale(lock, max_age_hours):
        lock.unlink(missing_ok=True)  # left behind by a killed or crashed run
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ScanLocked("Scan lain sedang berjalan") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def drop_unclosed_bar(df: pd.DataFrame, now: datetime, cutoff: str) -> pd.DataFrame:
    """Never conclude anything from a candle that has not closed yet."""
    if df.empty:
        return df
    hh, mm = map(int, cutoff.split(":"))
    if df.index[-1].date() == now.date() and (now.hour, now.minute) < (hh, mm):
        return df.iloc[:-1]
    return df


def recent_sessions(frames: dict[str, pd.DataFrame], n: int) -> list[pd.Timestamp]:
    """Last n trading sessions, inferred from dates that many tickers share."""
    counts: Counter = Counter()
    for df in frames.values():
        counts.update(df.index[-(n + 5):])
    if not counts:
        return []
    threshold = max(1, len(frames) * 0.2)
    sessions = sorted(d for d, c in counts.items() if c >= threshold)
    return sessions[-n:]


def _progress(stage: str, done: int = 0, total: int = 0) -> None:
    PROGRESS.update(stage=stage, done=done, total=total)


def update_prices(conn, source, tickers: list[str], cfg) -> dict[str, str]:
    last = store.last_price_dates(conn)
    groups: dict[str | None, list[str]] = {}
    for t in tickers:
        start = None
        if t in last:
            start = (datetime.strptime(last[t], "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        groups.setdefault(start, []).append(t)
    failed: dict[str, str] = {}
    done = 0
    for start, group in groups.items():
        offset = done

        def prog(i, n, offset=offset):
            _progress("Mengambil harga", offset + i, len(tickers))

        res = source.fetch_ohlcv(group, start=start, period=None if start else cfg.history.initial_period, progress=prog)
        for t, df in res.frames.items():
            store.upsert_prices(conn, t, df)
        failed.update(res.failed)
        done += len(group)
    return failed


def refresh_fundamentals(conn, fund_source, tickers: list[str]) -> int:
    _progress("Mengambil fundamental")
    funds = fund_source.fetch_fundamentals(tickers)
    week = market_now().strftime("%G-W%V")
    last_close = {
        r[0]: r[1] for r in conn.execute(
            "SELECT p.ticker, p.close FROM prices p JOIN (SELECT ticker, MAX(date) d FROM prices GROUP BY ticker) m "
            "ON p.ticker = m.ticker AND p.date = m.d")
    }
    for t, f in funds.items():
        store.upsert_fundamentals(conn, t, f)
        if t in last_close:
            per, pbv = valuation.ratios(f, last_close[t])
            store.save_snapshot(conn, t, week, per, pbv)
    return len(funds)


def fundamentals_due(funds: dict[str, dict], symbols: list[str], now: datetime, max_age_days: int = 7) -> bool:
    """Refresh when most of the universe has no data yet or the newest fetch is older than a week."""
    have = [funds[t] for t in symbols if t in funds]
    if len(have) < len(symbols) / 2:
        return True
    newest = max(datetime.fromisoformat(f["fetched_at"]) for f in have)
    return now.replace(tzinfo=None) - newest > timedelta(days=max_age_days)


def build_result(stock: universe.Stock, analysis: dict, val: dict) -> dict:
    setup = analysis["setup"]
    plan = setup["plan"] if setup else {}
    flags = list(analysis["flags"])
    if val["label"] == valuation.UNDER and analysis["trend"] == "TURUN":
        flags.append("value_trap")
    zones = [z.to_dict() for z in analysis["struct"]["zones"] if z.valid][:3]
    return {
        "ticker": stock.ticker, "close": analysis["close"], "change_pct": analysis["change_pct"],
        "score": analysis["score"], "trend": analysis["trend"],
        "setup_type": setup["type"] if setup else None,
        "zone_low": plan.get("zone_low"), "zone_high": plan.get("zone_high"),
        "entry": plan.get("entry"), "sl": plan.get("sl"), "tp1": plan.get("tp1"), "tp2": plan.get("tp2"),
        "rr": plan.get("rr"),
        "valuation_label": val["label"], "valuation_reason": val["reason"], "graham": val["graham"],
        "flags": flags, "reasons": analysis["reasons"],
        "detail": {
            "name": stock.name, "sector": stock.sector, "industry": stock.industry,
            "checklist": analysis["checklist"],
            "setup": {k: setup[k] for k in ("type", "triggered", "detail")} if setup else None,
            "plan": plan, "zones": zones,
            "valuation": {k: val[k] for k in ("per", "pbv", "ratio", "graham", "graham_mos", "history_note")},
        },
    }


def daily_scan(cfg=None, source=None, fund_source=None, tickers: list[str] | None = None,
               now: datetime | None = None, force: bool = False) -> int:
    cfg = cfg or config.get()
    source = source or YahooSource(cfg.fetch.retries, cfg.fetch.backoff_seconds)
    fund_source = fund_source or TradingViewFundamentals(cfg.fetch.retries, cfg.fetch.backoff_seconds)
    now = now or market_now()
    conn = store.connect(cfg.database)
    PROGRESS.update(running=True, stage="Mulai", done=0, total=0, error=None)
    try:
        with scan_lock(cfg.database):
            # holding the lock means no other scan is alive: any 'running' row was killed mid-way
            store.fail_orphaned_scans(conn, "Proses scan terhenti sebelum selesai")
            scan_id = store.create_scan(conn)
            PROGRESS["scan_id"] = scan_id
            try:
                return _run_scan(conn, cfg, source, fund_source, tickers, now, scan_id, force)
            except Exception as e:
                store.finish_scan(conn, scan_id, status="failed", note=f"{type(e).__name__}: {e}")
                PROGRESS["error"] = str(e)
                raise
    finally:
        PROGRESS["running"] = False
        conn.close()


def _run_scan(conn, cfg, source, fund_source, tickers, now, scan_id, force=False) -> int:
    stocks = universe.load(cfg.universe)
    if tickers:
        wanted = {t.upper() for t in tickers}
        stocks = [s for s in stocks if s.ticker in wanted]
    symbols = [s.ticker for s in stocks]

    failed = update_prices(conn, source, symbols, cfg)
    for t, why in failed.items():
        store.add_error(conn, scan_id, t, "fetch", why)

    if fundamentals_due(store.all_fundamentals(conn), symbols, now):
        try:
            refresh_fundamentals(conn, fund_source, symbols)
        except Exception as e:
            store.add_error(conn, scan_id, None, "fundamentals", f"{type(e).__name__}: {e}")

    _progress("Menghitung", 0, len(stocks))
    frames: dict[str, pd.DataFrame] = {}
    for s in stocks:
        df = drop_unclosed_bar(store.load_prices(conn, s.ticker), now, cfg.market.close_cutoff)
        if not df.empty:
            frames[s.ticker] = df

    sessions = recent_sessions(frames, cfg.market.stale_trading_days)
    if not sessions:
        store.finish_scan(conn, scan_id, status="failed", n_failed=len(failed), note="Tidak ada data harga")
        return scan_id
    session_date = sessions[-1].strftime("%Y-%m-%d")
    prev = store.latest_scan(conn)
    if prev and prev["scan_date"] == session_date and not (tickers or force):
        store.finish_scan(conn, scan_id, status="no_session", scan_date=session_date, n_failed=len(failed),
                          note=f"Tidak ada sesi baru sejak {session_date} (libur atau data belum update)")
        return scan_id

    fresh = {t: df for t, df in frames.items() if df.index[-1] >= sessions[0]}
    stale = [t for t in frames if t not in fresh]
    for t in stale:
        store.add_error(conn, scan_id, t, "stale", f"bar terakhir {frames[t].index[-1]:%Y-%m-%d}")

    funds = store.all_fundamentals(conn)
    closes = {t: float(df["close"].iloc[-1]) for t, df in fresh.items()}
    medians = valuation.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    history = store.snapshots(conn)

    results, n_filtered = [], 0
    by = universe.by_ticker(stocks)
    for i, (t, df) in enumerate(fresh.items(), 1):
        _progress("Menghitung", i, len(fresh))
        try:
            dfi = indicators.add_all(df)
            ok, _why = scoring.passes_filters(dfi, cfg)
            if not ok:
                n_filtered += 1
                continue
            analysis = scoring.analyze(dfi, cfg)
            val = valuation.label(by[t], funds.get(t), closes[t], medians, cfg, history.get(t))
            results.append(build_result(by[t], analysis, val))
        except Exception as e:
            log.exception("analyze %s", t)
            store.add_error(conn, scan_id, t, "analyze", f"{type(e).__name__}: {e}")

    store.save_results(conn, scan_id, results)
    store.finish_scan(
        conn, scan_id, status="done", scan_date=session_date, n_ok=len(results),
        n_failed=len(failed), n_stale=len(stale), n_filtered=n_filtered,
    )
    _progress("Selesai", len(fresh), len(fresh))
    return scan_id


def weekly_fundamentals(cfg=None, fund_source=None) -> int:
    cfg = cfg or config.get()
    fund_source = fund_source or TradingViewFundamentals(cfg.fetch.retries, cfg.fetch.backoff_seconds)
    conn = store.connect(cfg.database)
    try:
        return refresh_fundamentals(conn, fund_source, [s.ticker for s in universe.load(cfg.universe)])
    finally:
        conn.close()


def _log_to_file(path: str) -> None:
    """Under pythonw (scheduled runs) there is no console: send stdout/stderr and logging to a file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.jobs")
    p.add_argument("--log", help="append all output to this file (for scheduled runs under pythonw)")
    sub = p.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("daily_scan")
    scan.add_argument("--tickers", help="comma separated, e.g. BBCA,TLKM (default: whole universe)")
    scan.add_argument("--force", action="store_true", help="rescan even if there is no new session")
    sub.add_parser("weekly_fundamentals")
    bt = sub.add_parser("backtest")
    bt.add_argument("--tickers", help="comma separated (default: tickers in the latest scan)")
    bt.add_argument("--max-age-days", type=float,
                    help="skip when the latest backtest is younger than this (used by the scheduled workflow)")
    args = p.parse_args(argv)
    if args.log:
        _log_to_file(args.log)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)

    t0 = time.time()
    if args.cmd == "daily_scan":
        sid = daily_scan(tickers=args.tickers.split(",") if args.tickers else None, force=args.force)
        conn = store.connect(config.get().database)
        s = store.get_scan(conn, sid)
        print(f"scan #{sid} {s['status']} {s['scan_date']}: ok={s['n_ok']} filtered={s['n_filtered']} "
              f"failed={s['n_failed']} stale={s['n_stale']} ({time.time() - t0:.0f}s) {s['note'] or ''}")
    elif args.cmd == "weekly_fundamentals":
        print(f"fundamentals updated for {weekly_fundamentals()} tickers ({time.time() - t0:.0f}s)")
    elif args.cmd == "backtest":
        from app import backtest
        if args.max_age_days is not None:
            conn = store.connect(config.get().database)
            last = store.latest_backtest(conn)
            conn.close()
            if last and datetime.now() - datetime.fromisoformat(last["run_at"]) < timedelta(days=args.max_age_days):
                print(f"backtest skipped: latest run {last['run_at']} is younger than {args.max_age_days:g} days")
                return 0
        res = backtest.run(tickers=args.tickers.split(",") if args.tickers else None)
        print(backtest.summary_text(res))
        print(f"({time.time() - t0:.0f}s)")
    return 0


def entrypoint() -> int:
    try:
        return main()
    except Exception:
        log.exception("job failed")  # lands in the --log file when run headless
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
