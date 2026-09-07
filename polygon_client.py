"""Polygon market data: historical bars for backtesting, quotes for live pricing.

IBKR is execution only. Its paper account has no market data subscription, so
every quote and every bar comes from here. That also means the bot must use
LIMIT orders — IBKR rejects market orders when it has no feed of its own
(error 202, "No market data on major exchange for market order").
"""
from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.polygon.io"
CACHE = Path(__file__).parent / "data" / "cache"


class PolygonError(RuntimeError):
    pass


class Polygon:
    def __init__(self, api_key: str | None = None, *, calls_per_min: int = 5) -> None:
        self.key = api_key or os.environ.get("POLYGON_API_KEY")
        if not self.key:
            raise PolygonError(
                "POLYGON_API_KEY not set. Put it in ~/Documents/odte-bot/.env "
                "and load with: set -a && . ./.env && set +a"
            )
        # Free tier is 5 req/min. Self-throttle rather than eat 429s mid-backtest.
        self._min_interval = 60.0 / calls_per_min if calls_per_min else 0.0
        self._last_call = 0.0
        self._session = requests.Session()

    def _get(self, path: str, **params) -> dict:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        params["apiKey"] = self.key
        r = self._session.get(f"{BASE}{path}", params=params, timeout=30)
        self._last_call = time.monotonic()
        if r.status_code == 429:
            raise PolygonError("rate limited (429) — lower calls_per_min or upgrade plan")
        if r.status_code == 403:
            raise PolygonError(f"403 for {path} — plan does not cover this endpoint")
        r.raise_for_status()
        return r.json()

    def bars(
        self,
        ticker: str,
        start: str | date,
        end: str | date,
        *,
        multiplier: int = 1,
        timespan: str = "minute",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """OHLCV bars. Cached to parquet — never refetch the same window twice."""
        key = f"{ticker}_{multiplier}{timespan}_{start}_{end}".replace(":", "")
        cached = CACHE / f"{key}.parquet"
        if use_cache and cached.exists():
            return pd.read_parquet(cached)

        rows: list[dict] = []
        payload = self._get(
            f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start}/{end}",
            adjusted="true", sort="asc", limit=50000,
        )
        while True:
            rows.extend(payload.get("results", []))
            nxt = payload.get("next_url")
            if not nxt:
                break
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            r = self._session.get(nxt, params={"apiKey": self.key}, timeout=30)
            self._last_call = time.monotonic()
            r.raise_for_status()
            payload = r.json()

        if not rows:
            raise PolygonError(f"no bars returned for {ticker} {start}..{end}")

        df = pd.DataFrame(rows).rename(
            columns={"t": "ts", "o": "open", "h": "high", "l": "low",
                     "c": "close", "v": "volume", "n": "trades", "vw": "vwap"}
        )
        # Polygon stamps ms UTC. Bot logic is all in exchange time.
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("America/New_York")
        df = df.set_index("ts").sort_index()
        cached.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cached)
        return df

    def last_quote(self, ticker: str) -> dict:
        """NBBO for a stock. Used to price limit orders."""
        return self._get(f"/v2/last/nbbo/{ticker}").get("results", {})

    def option_chain(self, underlying: str, expiry: str) -> pd.DataFrame:
        """Chain snapshot for one expiry. expiry = YYYY-MM-DD."""
        out, cursor = [], None
        while True:
            params = {"expiration_date": expiry, "limit": 250}
            if cursor:
                params["cursor"] = cursor
            payload = self._get(f"/v3/snapshot/options/{underlying}", **params)
            out.extend(payload.get("results", []))
            nxt = payload.get("next_url")
            if not nxt:
                break
            cursor = nxt.split("cursor=")[-1]
        if not out:
            raise PolygonError(f"empty chain for {underlying} {expiry}")
        return pd.json_normalize(out)

    @staticmethod
    def occ(underlying: str, expiry: date, right: str, strike: float) -> str:
        """OCC option ticker, e.g. O:SPY260907C00650000."""
        return (
            f"O:{underlying}{expiry:%y%m%d}{right.upper()}"
            f"{int(round(strike * 1000)):08d}"
        )


if __name__ == "__main__":
    p = Polygon()
    df = p.bars("SPY", "2026-09-02", "2026-09-05")
    print(df.tail())
    print("rows:", len(df))
