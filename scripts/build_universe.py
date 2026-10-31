"""Regenerate data/universe.csv (IDX common stocks + sector).

idx.co.id blocks scripted requests, so the list comes from TradingView's public
screener endpoint. Run occasionally to pick up new listings:

    .venv/Scripts/python scripts/build_universe.py
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "universe.csv"
URL = "https://scanner.tradingview.com/indonesia/scan"

FINANCIAL_INDUSTRIES = {
    "Regional Banks", "Major Banks", "Savings Banks", "Financial Conglomerates",
    "Investment Banks/Brokers", "Investment Managers", "Finance/Rental/Leasing",
    "Life/Health Insurance", "Property/Casualty Insurance", "Multi-Line Insurance",
    "Insurance Brokers/Services", "Specialty Insurance",
}


def fetch() -> list[list[str]]:
    body = json.dumps({
        "columns": ["name", "description", "sector", "industry", "subtype"],
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "range": [0, 5000],
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    return [row["d"] for row in payload["data"]]


def main() -> int:
    rows = []
    for ticker, name, sector, industry, subtype in fetch():
        if subtype not in (None, "common") or not ticker.isalpha():
            continue
        sector = sector or "Unknown"
        industry = industry or "Unknown"
        if sector == "Finance" and "Real Estate" in industry:
            sector = "Real Estate"
        rows.append({
            "ticker": ticker,
            "name": name,
            "sector": sector,
            "industry": industry,
            "is_financial": int(industry in FINANCIAL_INDUSTRIES),
        })
    rows.sort(key=lambda r: r["ticker"])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "sector", "industry", "is_financial"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} tickers to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
