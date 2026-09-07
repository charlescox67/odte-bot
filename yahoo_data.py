"""Free underlying bars from Yahoo. Same shape as polygon_client.bars().

Yahoo has NO historical option chains, so this validates the SIGNAL layer only.
Measured limits: 1m -> 8 days per request; 5m -> 60 days; 1h -> 730 days.
"""
from __future__ import annotations
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, yfinance as yf

def bars(ticker: str = "SPY", period: str = "60d", interval: str = "5m") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval,
                     progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"no data: {ticker} {period}/{interval}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = df.index.tz_convert("America/New_York")
    return df[df.index.to_series().dt.time.between(
        pd.Timestamp("09:30").time(), pd.Timestamp("15:59").time())]
