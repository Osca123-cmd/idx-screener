"""Static site generator for GitHub Pages: `python -m app.site --out _site`.

Renders the same Jinja templates as the local web app, with relative links, client-side
filtering and a browser-local watchlist instead of server endpoints.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import chartdata, config, store, universe
from app.jobs import JAKARTA
from app.web import fmt

WEB = Path(__file__).parent / "web"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape(["html"]))
    fmt.register(env)
    return env


def build(out: Path, cfg=None, repo_url: str | None = None) -> dict:
    cfg = cfg or config.get()
    conn = store.connect(cfg.database)
    try:
        scan = store.latest_scan(conn)
        last_attempt = store.latest_scan(conn, status=None)
        rows = store.scan_results(conn, scan["id"]) if scan else []
        run = store.latest_backtest(conn)
        stocks = universe.by_ticker(universe.load(cfg.universe))
        histories = {r["ticker"]: store.score_history(conn, r["ticker"]) for r in rows}
        prices = {r["ticker"]: store.load_prices(conn, r["ticker"]) for r in rows}
    finally:
        conn.close()

    if out.exists():
        shutil.rmtree(out)
    (out / "stock").mkdir(parents=True)
    (out / "data" / "chart").mkdir(parents=True)
    shutil.copytree(WEB / "static", out / "static")
    (out / ".nojekyll").write_text("")

    env = _env()
    pages = {r["ticker"] for r in rows}
    common = {
        "scan": scan, "last_attempt": last_attempt, "repo_url": repo_url,
        "built_at": datetime.now(JAKARTA).strftime("%d-%m-%Y %H:%M WIB"),
        "scan_date": scan["scan_date"] if scan else None,
    }

    def render(template: str, path: str, root: str, **ctx) -> None:
        html = env.get_template(template).render(**common, **fmt.site_links(root, pages), root=root, **ctx)
        (out / path).write_text(html, encoding="utf-8")

    rows_sorted = sorted(rows, key=lambda r: (-(r["score"] or 0), r["ticker"]))
    render("screener.html", "index.html", "", rows=rows_sorted, total=len(rows), q={},
           sectors=sorted({r["detail"].get("sector") for r in rows if r["detail"].get("sector")}))
    render("backtest.html", "backtest.html", "", run=run, bt={"running": False, "error": None})
    render("watchlist_static.html", "watchlist.html", "",
           latest={r["ticker"]: {"score": r["score"], "trend": r["trend"]} for r in rows})

    for r in rows:
        t = r["ticker"]
        render("stock.html", f"stock/{t}.html", "../", stock=stocks[t], r=r, note=None,
               history=histories[t], watched=False)
        if not prices[t].empty:
            (out / "data" / "chart" / f"{t}.json").write_text(
                json.dumps(chartdata.payload(prices[t]), separators=(",", ":")), encoding="utf-8")

    return {"pages": len(pages) + 3, "scan_date": common["scan_date"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.site")
    p.add_argument("--out", default="_site")
    p.add_argument("--repo-url", help="link for the manual-scan button, e.g. https://github.com/user/repo")
    args = p.parse_args(argv)
    info = build(Path(args.out), repo_url=args.repo_url)
    print(f"built {info['pages']} pages for scan {info['scan_date']} into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
