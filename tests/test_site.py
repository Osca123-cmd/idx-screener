"""Static site build from a fake-source scan."""
import json
from datetime import datetime

from app import jobs, site
from tests.test_jobs import JKT, FakeFunds, FakePrices, make_cfg


def test_build_static_site(tmp_path):
    cfg = make_cfg(tmp_path)
    jobs.daily_scan(cfg, source=FakePrices(), fund_source=FakeFunds(), now=datetime(2026, 10, 30, 17, 0, tzinfo=JKT))
    out = tmp_path / "_site"
    info = site.build(out, cfg, repo_url="https://github.com/u/r")
    assert info["scan_date"] == "2026-10-30"

    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="stock/UPPP.html"' in index and 'data-trend="NAIK"' in index
    assert "hx-get" not in index and "htmx" not in index          # no server endpoints in static mode
    assert "https://github.com/u/r/actions" in index

    stock = (out / "stock" / "UPPP.html").read_text(encoding="utf-8")
    assert 'data-src="../data/chart/UPPP.json"' in stock and 'id="watch-btn"' in stock
    assert 'href="../static/app.css' in stock

    chart = json.loads((out / "data" / "chart" / "UPPP.json").read_text())
    assert chart["candles"] and chart["ema200"]
    assert (out / "watchlist.html").exists() and (out / "backtest.html").exists()
    assert (out / ".nojekyll").exists() and (out / "static" / "watch.js").exists()
    assert not (out / "stock" / "THIN.html").exists()             # filtered tickers get no page
