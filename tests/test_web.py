"""Web smoke tests against a temp DB filled by the fake-source scan."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import config, jobs
from tests.test_jobs import JKT, FakeFunds, FakePrices, make_cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    jobs.daily_scan(cfg, source=FakePrices(), fund_source=FakeFunds(), now=datetime(2026, 10, 30, 17, 0, tzinfo=JKT))
    monkeypatch.setattr(config, "get", lambda: cfg)
    from app.web import main
    return TestClient(main.app)


def test_screener_renders_and_filters(client):
    r = client.get("/")
    assert r.status_code == 200 and "UPPP" in r.text and "Papan" in r.text
    r = client.get("/?trend=TURUN", headers={"HX-Request": "true", "HX-Target": "results"})
    assert "DOWN" in r.text and "UPPP" not in r.text and "<html" not in r.text


def test_stock_detail_and_chart(client):
    r = client.get("/stock/UPPP")
    assert r.status_code == 200 and "Trend harian: NAIK" in r.text and "Checklist skor" in r.text
    data = client.get("/api/chart/UPPP").json()
    assert len(data["candles"]) == 260 and data["ema200"]


def test_filtered_ticker_computed_on_the_fly(client):
    r = client.get("/stock/THIN")
    assert r.status_code == 200 and "Tidak masuk screener" in r.text


def test_watchlist_roundtrip(client):
    assert client.post("/watchlist/UPPP", follow_redirects=False).status_code == 303
    r = client.get("/watchlist")
    assert "UPPP" in r.text and ("Menunggu entry" in r.text or "Entry tersentuh" in r.text)
    client.post("/watchlist/UPPP/delete")
    assert "Watchlist kosong" in client.get("/watchlist").text


def test_backtest_page_empty(client):
    r = client.get("/backtest")
    assert r.status_code == 200 and "Belum ada backtest" in r.text


def test_unknown_ticker_404(client):
    assert client.get("/stock/NOPE").status_code == 404
