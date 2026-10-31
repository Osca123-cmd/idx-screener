"""Fundamentals from TradingView's public screener endpoint (unofficial, free).

One request returns EPS/BVPS/PER/PBV/ROE for every IDX stock, versus ~850 slow
per-ticker calls on Yahoo.
"""
from __future__ import annotations

import json
import time
import urllib.request

URL = "https://scanner.tradingview.com/indonesia/scan"
COLUMNS = [
    "name", "earnings_per_share_diluted_ttm", "book_value_per_share_fq", "price_earnings_ttm",
    "price_book_fq", "return_on_equity", "market_cap_basic", "fundamental_currency_code",
]


def parse_scan(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in payload.get("data", []):
        name, eps, bvps, per, pbv, roe, mcap, ccy = row["d"]
        if eps is None and bvps is None:
            continue
        f = {
            "eps": eps, "bvps": bvps, "per": per, "pbv": pbv,
            "roe": roe / 100 if roe is not None else None,
            "market_cap": mcap,
        }
        if ccy not in (None, "IDR"):
            # EPS/BVPS in USD against IDR prices would produce nonsense ratios.
            f["eps"] = f["bvps"] = f["per"] = f["pbv"] = None
        out[name] = f
    return out


class TradingViewFundamentals:
    def __init__(self, retries: int = 3, backoff: float = 2.0, timeout: float = 60):
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def fetch_fundamentals(self, tickers: list[str]) -> dict[str, dict]:
        body = {
            "columns": COLUMNS,
            "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
            "range": [0, 5000],
        }
        for attempt in range(1, self.retries + 1):
            try:
                data = parse_scan(self._post(body))
                wanted = set(tickers)
                return {t: f for t, f in data.items() if t in wanted}
            except Exception:
                if attempt == self.retries:
                    raise
                time.sleep(self.backoff * attempt)
        return {}
