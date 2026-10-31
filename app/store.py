"""SQLite persistence. The web app only ever reads from here."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT PRIMARY KEY, fetched_at TEXT NOT NULL,
    eps REAL, bvps REAL, per REAL, pbv REAL, roe REAL, market_cap REAL
);
CREATE TABLE IF NOT EXISTS valuation_snapshots (
    ticker TEXT NOT NULL, week TEXT NOT NULL, per REAL, pbv REAL,
    PRIMARY KEY (ticker, week)
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date TEXT, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, n_ok INTEGER DEFAULT 0, n_failed INTEGER DEFAULT 0,
    n_stale INTEGER DEFAULT 0, n_filtered INTEGER DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS scan_results (
    scan_id INTEGER NOT NULL REFERENCES scans(id), ticker TEXT NOT NULL,
    close REAL, change_pct REAL, score INTEGER, trend TEXT, setup_type TEXT,
    zone_low REAL, zone_high REAL, entry REAL, sl REAL, tp1 REAL, tp2 REAL, rr REAL,
    valuation_label TEXT, valuation_reason TEXT, graham REAL,
    flags_json TEXT, reasons_json TEXT, detail_json TEXT,
    PRIMARY KEY (scan_id, ticker)
);
CREATE INDEX IF NOT EXISTS ix_scan_results_ticker ON scan_results(ticker);
CREATE TABLE IF NOT EXISTS scan_errors (
    scan_id INTEGER NOT NULL, ticker TEXT, stage TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    ticker TEXT PRIMARY KEY, added_at TEXT NOT NULL, plan_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL,
    params_json TEXT, results_json TEXT
);
"""

RESULT_COLUMNS = [
    "ticker", "close", "change_pct", "score", "trend", "setup_type", "zone_low", "zone_high",
    "entry", "sl", "tp1", "tp2", "rr", "valuation_label", "valuation_reason", "graham",
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# --- prices -----------------------------------------------------------------

def upsert_prices(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = [
        (ticker, d.strftime("%Y-%m-%d"), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume))
        for d, r in zip(df.index, df.itertuples(index=False))
    ]
    conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def load_prices(conn: sqlite3.Connection, ticker: str, start: str | None = None) -> pd.DataFrame:
    sql = "SELECT date, open, high, low, close, volume FROM prices WHERE ticker=?"
    args: list = [ticker]
    if start:
        sql += " AND date>=?"
        args.append(start)
    df = pd.read_sql_query(sql + " ORDER BY date", conn, params=args)
    df.index = pd.to_datetime(df.pop("date"))
    return df


def last_price_dates(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT ticker, MAX(date) FROM prices GROUP BY ticker")}


def delete_prices_from(conn: sqlite3.Connection, date: str) -> None:
    conn.execute("DELETE FROM prices WHERE date>=?", (date,))
    conn.commit()


# --- fundamentals -----------------------------------------------------------

def upsert_fundamentals(conn: sqlite3.Connection, ticker: str, f: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
        (ticker, now(), f.get("eps"), f.get("bvps"), f.get("per"), f.get("pbv"), f.get("roe"), f.get("market_cap")),
    )
    conn.commit()


def all_fundamentals(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["ticker"]: dict(r) for r in conn.execute("SELECT * FROM fundamentals")}


def save_snapshot(conn: sqlite3.Connection, ticker: str, week: str, per, pbv) -> None:
    conn.execute("INSERT OR REPLACE INTO valuation_snapshots VALUES (?,?,?,?)", (ticker, week, per, pbv))
    conn.commit()


def snapshots(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM valuation_snapshots ORDER BY week"):
        out.setdefault(r["ticker"], []).append(dict(r))
    return out


# --- scans ------------------------------------------------------------------

def create_scan(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO scans (started_at, status) VALUES (?, 'running')", (now(),))
    conn.commit()
    return cur.lastrowid


def finish_scan(conn: sqlite3.Connection, scan_id: int, **fields) -> None:
    fields.setdefault("finished_at", now())
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE scans SET {cols} WHERE id=?", (*fields.values(), scan_id))
    conn.commit()


def add_error(conn: sqlite3.Connection, scan_id: int, ticker: str | None, stage: str, message: str) -> None:
    conn.execute("INSERT INTO scan_errors VALUES (?,?,?,?)", (scan_id, ticker, stage, message[:500]))
    conn.commit()


def save_results(conn: sqlite3.Connection, scan_id: int, results: list[dict]) -> None:
    rows = []
    for r in results:
        rows.append((
            scan_id, *[r.get(c) for c in RESULT_COLUMNS],
            json.dumps(r.get("flags", [])), json.dumps(r.get("reasons", [])), json.dumps(r.get("detail", {})),
        ))
    placeholders = ",".join("?" * (len(RESULT_COLUMNS) + 4))
    conn.executemany(f"INSERT OR REPLACE INTO scan_results VALUES ({placeholders})", rows)
    conn.commit()


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["flags"] = json.loads(d.pop("flags_json") or "[]")
    d["reasons"] = json.loads(d.pop("reasons_json") or "[]")
    d["detail"] = json.loads(d.pop("detail_json") or "{}")
    return d


def latest_scan(conn: sqlite3.Connection, status: str | None = "done") -> dict | None:
    sql = "SELECT * FROM scans"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
    return dict(row) if row else None


def get_scan(conn: sqlite3.Connection, scan_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    return dict(row) if row else None


def scan_results(conn: sqlite3.Connection, scan_id: int) -> list[dict]:
    return [_decode(r) for r in conn.execute("SELECT * FROM scan_results WHERE scan_id=?", (scan_id,))]


def ticker_result(conn: sqlite3.Connection, scan_id: int, ticker: str) -> dict | None:
    row = conn.execute("SELECT * FROM scan_results WHERE scan_id=? AND ticker=?", (scan_id, ticker)).fetchone()
    return _decode(row) if row else None


def scan_errors(conn: sqlite3.Connection, scan_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM scan_errors WHERE scan_id=?", (scan_id,))]


def score_history(conn: sqlite3.Connection, ticker: str, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT s.scan_date, r.score, r.trend, r.setup_type FROM scan_results r
           JOIN scans s ON s.id = r.scan_id
           WHERE r.ticker=? AND s.id IN (SELECT MAX(id) FROM scans WHERE status='done' GROUP BY scan_date)
           ORDER BY s.id DESC LIMIT ?""",
        (ticker, limit),
    )
    return [dict(r) for r in rows][::-1]


# --- watchlist --------------------------------------------------------------

def watch_add(conn: sqlite3.Connection, ticker: str, plan: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO watchlist VALUES (?,?,?)", (ticker, now(), json.dumps(plan)))
    conn.commit()


def watch_remove(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
    conn.commit()


def watch_list(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC"):
        d = dict(r)
        d["plan"] = json.loads(d.pop("plan_json"))
        out.append(d)
    return out


# --- backtest ---------------------------------------------------------------

def save_backtest(conn: sqlite3.Connection, params: dict, results: dict) -> int:
    cur = conn.execute(
        "INSERT INTO backtest_runs (run_at, params_json, results_json) VALUES (?,?,?)",
        (now(), json.dumps(params), json.dumps(results)),
    )
    conn.commit()
    return cur.lastrowid


def latest_backtest(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d.pop("params_json"))
    d["results"] = json.loads(d.pop("results_json"))
    return d
