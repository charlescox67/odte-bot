"""Trend-pullback swing signal on 5-minute bars. Pure: no network, no clock.

Trade WITH the day's trend. In an uptrend, wait for a pullback that holds a
higher low, then enter when price turns back up; the stop sits under that low
and ratchets up to each newer swing low while the trend runs. Downtrends are
the exact mirror with swing highs and puts.

Everything is evaluated strictly at `asof` (the lagged clock run_bot uses):
only bars that had fully COMPLETED by then are visible. A 1-minute bar
labelled 10:34 covers 10:34:00-10:34:59, so it is complete only at 10:35.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import pandas as pd

PIVOT_K = 2          # a pivot must beat the 2 bars on each side
FRESH_BARS = 6       # the pivot must have been confirmed within 30 min
EMA_FAST, EMA_SLOW = 9, 21
MIN_BARS = 6         # 09:30-10:00 before any setup can exist
SESSION_OPEN = time(9, 30)


@dataclass(frozen=True)
class Setup:
    side: str        # "C" long / "P" short
    price: float     # underlying, last completed 1m close at asof
    stop: float      # underlying level: the higher low (or lower high)
    pivot_id: str    # e.g. "L1035" — one trade per pivot


def complete_1m(df_1m: pd.DataFrame, asof: datetime) -> pd.DataFrame:
    d = df_1m[df_1m.index + timedelta(minutes=1) <= asof]
    return d[d.index.time >= SESSION_OPEN]


def to_5m(df_1m: pd.DataFrame, asof: datetime) -> pd.DataFrame:
    """Completed 5-minute bars only — nothing that was still forming at asof."""
    d = complete_1m(df_1m, asof)
    if d.empty:
        return d
    b = d.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna(subset=["close"])
    return b[b.index + timedelta(minutes=5) <= asof]


def session_vwap(df_1m: pd.DataFrame, asof: datetime) -> float | None:
    d = complete_1m(df_1m, asof)
    if d.empty:
        return None
    tp = (d["high"] + d["low"] + d["close"]) / 3
    vol = d["volume"].fillna(0)
    if vol.sum() <= 0:          # index feeds report no volume
        return float(tp.mean())
    return float((tp * vol).sum() / vol.sum())


def pivots(b: pd.DataFrame, kind: str, k: int = PIVOT_K) -> list[int]:
    """Indices of CONFIRMED swing lows ('low') or highs ('high').

    A pivot at i needs k completed bars after it, so the last k bars can never
    be pivots yet — that is the confirmation delay, not a bug."""
    col = b[kind].to_numpy()
    out = []
    for i in range(k, len(col) - k):
        left, right = col[i - k:i], col[i + 1:i + 1 + k]
        if kind == "low" and col[i] < left.min() and col[i] < right.min():
            out.append(i)
        if kind == "high" and col[i] > left.max() and col[i] > right.max():
            out.append(i)
    return out


def trend(b: pd.DataFrame, vwap: float) -> str | None:
    if len(b) < MIN_BARS:
        return None
    c = b["close"]
    fast = c.ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
    slow = c.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-1]
    last = c.iloc[-1]
    if last > vwap and fast > slow:
        return "up"
    if last < vwap and fast < slow:
        return "down"
    return None


def swing_setup(df_1m: pd.DataFrame, asof: datetime,
                traded: set[str] = frozenset()) -> Setup | None:
    b = to_5m(df_1m, asof)
    vwap = session_vwap(df_1m, asof)
    if vwap is None or len(b) < MIN_BARS:
        return None
    t = trend(b, vwap)
    if t is None:
        return None
    long = t == "up"
    kind = "low" if long else "high"
    piv = pivots(b, kind)
    if len(piv) < 2:
        return None
    i_prev, i = piv[-2], piv[-1]
    p_prev, p = b[kind].iloc[i_prev], b[kind].iloc[i]
    # higher low (or lower high)
    if (long and p <= p_prev) or (not long and p >= p_prev):
        return None
    # still fresh: confirmed (i + K) within the last FRESH_BARS bars
    if (len(b) - 1) - (i + PIVOT_K) >= FRESH_BARS:
        return None
    # pullback intact: no close through the pivot since it formed
    after = b["close"].iloc[i + 1:]
    if (long and (after < p).any()) or (not long and (after > p).any()):
        return None
    # resumption: the last bar closed beyond the previous bar's extreme
    last, prev = b.iloc[-1], b.iloc[-2]
    if long and not last["close"] > prev["high"]:
        return None
    if not long and not last["close"] < prev["low"]:
        return None
    pid = f"{'L' if long else 'H'}{b.index[i]:%H%M}"
    if pid in traded:
        return None
    price = float(complete_1m(df_1m, asof)["close"].iloc[-1])
    if (long and price <= p) or (not long and price >= p):
        return None
    return Setup("C" if long else "P", price, float(p), pid)


def trail_stop(df_1m: pd.DataFrame, asof: datetime, side: str, stop: float) -> float:
    """Ratchet toward price: the newest confirmed swing low (high for puts),
    only if it is beyond the current stop. Never loosens."""
    b = to_5m(df_1m, asof)
    kind = "low" if side == "C" else "high"
    piv = pivots(b, kind)
    if not piv:
        return stop
    newest = float(b[kind].iloc[piv[-1]])
    return max(stop, newest) if side == "C" else min(stop, newest)


def stop_hit(df_1m: pd.DataFrame, asof: datetime, side: str, stop: float) -> bool:
    d = complete_1m(df_1m, asof)
    if d.empty:
        return False
    px = d["close"].iloc[-1]
    return px < stop if side == "C" else px > stop


def summary(df_1m: pd.DataFrame, asof: datetime) -> str:
    """One log line: what the signal sees, so a 'no setup' is reviewable."""
    b = to_5m(df_1m, asof)
    vwap = session_vwap(df_1m, asof)
    if vwap is None or len(b) < MIN_BARS:
        return f"warming up ({len(b)} bars)"
    t = trend(b, vwap) or "none"
    lows, highs = pivots(b, "low"), pivots(b, "high")
    lo = f"{b['low'].iloc[lows[-1]]:.2f}@{b.index[lows[-1]]:%H%M}" if lows else "-"
    hi = f"{b['high'].iloc[highs[-1]]:.2f}@{b.index[highs[-1]]:%H%M}" if highs else "-"
    return f"trend {t} | vwap {vwap:.2f} | last low {lo} high {hi} | close {b['close'].iloc[-1]:.2f}"


# ---- profit-taking: hard breakout vs slow grind ---------------------------
BREAKOUT_BARS = 10       # look at the last 10 one-minute candles
BREAKOUT_EFFICIENCY = 0.4  # >= 40% of all movement in one direction
BREAKOUT_MOVE_R = 0.5    # and covering at least 0.5R in those 10 minutes
DEEP_DIP_MULT = 2.5      # a red candle 2.5x the size of a typical candle
DIP_LOOKBACK = 12        # ...typical over the last hour of 5-minute candles


def efficiency(df_1m: pd.DataFrame, asof: datetime, n: int = BREAKOUT_BARS) -> float | None:
    """Net move / total distance travelled over the last n candles.
    1.0 = a straight line; near 0 = all ups and downs, going nowhere."""
    c = complete_1m(df_1m, asof)["close"]
    if len(c) < n + 1:
        return None
    path = c.diff().abs().iloc[-n:].sum()
    return None if path <= 0 else float(abs(c.iloc[-1] - c.iloc[-1 - n]) / path)


def breaking_out(df_1m: pd.DataFrame, asof: datetime, side: str, risk: float) -> bool:
    """Breaking out HARD: fast (>= 0.5R in 10 minutes, in the trade's direction)
    AND clean (efficiency >= 0.4). Loosened 2026-09-22: at the old 1R/0.5 a
    steady QQQ trend failed the test and was sold at +68%, then more than
    doubled. Over 18 days of 1-minute data the looser test doubles the number
    of runners (3 -> 6) while they still average about +2.5R."""
    c = complete_1m(df_1m, asof)["close"]
    er = efficiency(df_1m, asof)
    if er is None or risk <= 0:
        return False
    sign = 1.0 if side == "C" else -1.0
    move = sign * (c.iloc[-1] - c.iloc[-1 - BREAKOUT_BARS])
    return er >= BREAKOUT_EFFICIENCY and move >= BREAKOUT_MOVE_R * risk


def deep_dip(df_1m: pd.DataFrame, asof: datetime, side: str) -> bool:
    """The last completed 5-MINUTE candle went hard AGAINST the trade: a body
    at least DEEP_DIP_MULT times the typical 5-minute move of the last hour.

    Measured on 5-minute candles, not 1-minute: on 2026-09-22 the 1-minute
    version fired 7 minutes into a runner and would have sold it at +23%,
    worse than simply taking the +60% target. The move it was meant to catch
    ran to +173%. This is also the candle a person watching a chart sees.
    """
    b = to_5m(df_1m, asof)
    if len(b) < DIP_LOOKBACK + 2:
        return False
    last = b.iloc[-1]
    against = (last["open"] - last["close"]) if side == "C" else (last["close"] - last["open"])
    typical = b["close"].diff().abs().iloc[-DIP_LOOKBACK - 1:-1].median()
    return bool(typical > 0 and against >= DEEP_DIP_MULT * typical)


# ---- recorded context (not filters): Bollinger Bands, strength vs the Dow --
# Screened 2026-09-22 and NOT used as rules: band breaks continued ~50% of the
# time on SPY/QQQ 1-minute data, and relative strength vs the Dow did not
# separate winners from losers over 60 days (for SPY it pointed the wrong
# way). They are recorded on every trade so real results can settle it.
BB_BARS, BB_STD = 20, 2.0


def bollinger(df_1m: pd.DataFrame, asof: datetime) -> tuple[float, bool] | None:
    """(%B, squeeze) on 5-minute bars. %B: 0 = lower band, 0.5 = middle,
    1 = upper band, >1 = closed above it. Squeeze: band width in the bottom
    fifth of the session so far, the setup that often precedes a breakout."""
    c = to_5m(df_1m, asof)["close"]
    if len(c) < BB_BARS:
        return None
    mid = c.rolling(BB_BARS).mean(); sd = c.rolling(BB_BARS).std()
    up, lo = mid + BB_STD * sd, mid - BB_STD * sd
    span = (up - lo).iloc[-1]
    if not span > 0:
        return None
    width = ((up - lo) / mid).dropna()
    return float((c.iloc[-1] - lo.iloc[-1]) / span), bool(width.iloc[-1] <= width.quantile(0.2))


def vs_dow(sym_1m: pd.DataFrame, dow_1m: pd.DataFrame, asof: datetime,
           minutes: int = 30) -> float | None:
    """Relative strength: % the symbol beat (+) or lagged (-) the Dow over
    the last `minutes`, measured on completed candles at asof."""
    a = complete_1m(sym_1m, asof)["close"]
    b = complete_1m(dow_1m, asof)["close"].reindex(a.index).dropna()
    a = a.reindex(b.index)
    if len(a) < minutes + 1:
        return None
    return float((a.iloc[-1] / a.iloc[-1 - minutes] - b.iloc[-1] / b.iloc[-1 - minutes]) * 100)
