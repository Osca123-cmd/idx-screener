import pytest

from app import store
from tests.helpers import frame


@pytest.fixture
def conn(tmp_path):
    c = store.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def test_prices_roundtrip_and_upsert(conn):
    df = frame([100, 101, 102])
    assert store.upsert_prices(conn, "AAAA", df) == 3
    store.upsert_prices(conn, "AAAA", frame([100, 101, 105]))  # overwrite same dates
    back = store.load_prices(conn, "AAAA")
    assert list(back["close"]) == [100, 101, 105]
    assert store.last_price_dates(conn) == {"AAAA": back.index[-1].strftime("%Y-%m-%d")}


def test_scan_lifecycle(conn):
    sid = store.create_scan(conn)
    store.save_results(conn, sid, [{"ticker": "AAAA", "score": 80, "flags": ["x"], "reasons": ["r"], "detail": {"k": 1}}])
    store.add_error(conn, sid, "BBBB", "fetch", "boom")
    store.finish_scan(conn, sid, status="done", scan_date="2026-10-30", n_ok=1, n_failed=1)
    assert store.latest_scan(conn)["id"] == sid
    [r] = store.scan_results(conn, sid)
    assert r["score"] == 80 and r["flags"] == ["x"] and r["detail"] == {"k": 1}
    assert store.ticker_result(conn, sid, "AAAA")["reasons"] == ["r"]
    assert store.scan_errors(conn, sid)[0]["ticker"] == "BBBB"
    assert store.score_history(conn, "AAAA") == [{"scan_date": "2026-10-30", "score": 80, "trend": None, "setup_type": None}]


def test_running_scan_not_latest_done(conn):
    store.create_scan(conn)
    assert store.latest_scan(conn) is None
    assert store.latest_scan(conn, status=None)["status"] == "running"


def test_watchlist_and_fundamentals(conn):
    store.watch_add(conn, "AAAA", {"entry": 100})
    assert store.watch_list(conn)[0]["plan"] == {"entry": 100}
    store.watch_remove(conn, "AAAA")
    assert store.watch_list(conn) == []
    store.upsert_fundamentals(conn, "AAAA", {"eps": 10, "pbv": 1.2})
    assert store.all_fundamentals(conn)["AAAA"]["eps"] == 10
    store.save_snapshot(conn, "AAAA", "2026-W44", 10.0, 1.2)
    assert store.snapshots(conn)["AAAA"][0]["pbv"] == 1.2


def test_backtest_roundtrip(conn):
    store.save_backtest(conn, {"h": 20}, {"breakout": {"n": 3}})
    assert store.latest_backtest(conn)["results"]["breakout"]["n"] == 3
