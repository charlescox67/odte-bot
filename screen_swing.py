"""Screen the swing rule on 60 days of 5-minute bars. UNDERLYING ONLY.

Calls the SAME swing_setup / trail_stop / stop_hit the bot trades with, and
replays each day bar by bar with the bot's entry window, one-position and
3-per-day limits, 60-minute time stop and 15:45 flatten. Outcomes are in R,
where 1R = distance from entry to the initial stop.

What this cannot see: option prices. Yahoo has no historical chains, so theta,
spreads and the 50% premium backstop are absent. A 0DTE option loses to time
decay even when the underlying goes nowhere, so treat a small positive R as
break-even at best. This is screening, not a backtest.
"""
from __future__ import annotations

from datetime import time, timedelta

import pandas as pd

import swing_signal as sw
from run_bot import (ENTRY_START, NO_ENTRY_AFTER, MAX_ENTRIES_PER_DAY,
                     TIME_STOP_MIN, TIME_STOP_KEEP_R)
from yahoo_data import bars

FLATTEN_BAR = time(14, 40)  # the bar ending 14:45 (run_bot.FLATTEN_AT)


def replay(day: pd.DataFrame) -> list[dict]:
    trades, pos, traded = [], None, set()
    for k, t in enumerate(day.index):
        asof = t + timedelta(minutes=5, seconds=1)
        px = float(day["close"].iloc[k])
        if pos:
            pos["stop"] = sw.trail_stop(day, asof, pos["side"], pos["stop"])
            sign = 1 if pos["side"] == "C" else -1
            r_now = sign * (px - pos["px"]) / pos["risk"]
            aged = asof - pos["t"] >= timedelta(minutes=TIME_STOP_MIN)
            why = ("swing_stop" if sw.stop_hit(day, asof, pos["side"], pos["stop"]) else
                   "time_stop" if aged and r_now < TIME_STOP_KEEP_R else
                   "eod" if t.time() >= FLATTEN_BAR else None)
            if why:
                trades.append({"r": r_now, "why": why,
                               "mins": (asof - pos["t"]).seconds // 60})
                pos = None
        if pos is None and ENTRY_START <= asof.time() < NO_ENTRY_AFTER \
                and len(traded) < MAX_ENTRIES_PER_DAY:
            s = sw.swing_setup(day, asof, traded)
            if s:
                traded.add(s.pivot_id)
                pos = {"side": s.side, "px": s.price, "stop": s.stop,
                       "risk": abs(s.price - s.stop), "t": asof}
    return trades


def screen(sym: str) -> None:
    df = bars(sym, "60d", "5m")
    days = [d for _, d in df.groupby(df.index.date)]
    t = pd.DataFrame([tr for d in days for tr in replay(d)])
    if t.empty:
        print(f"{sym}: no trades in {len(days)} days"); return
    print(f"\n{sym}: {len(t)} trades over {len(days)} days "
          f"({len(t)/len(days):.1f}/day, {(t.r > 0).mean():.0%} winners)")
    print(f"  mean {t.r.mean():+.2f}R  median {t.r.median():+.2f}R  "
          f"total {t.r.sum():+.1f}R  | avg win {t.r[t.r>0].mean():+.2f}R "
          f"avg loss {t.r[t.r<=0].mean():+.2f}R")
    print(f"  median hold {t.mins.median():.0f} min | exits: "
          + ", ".join(f"{k} {v}" for k, v in t.why.value_counts().items()))
    half = len(t) // 2
    print(f"  first half {t.r.iloc[:half].mean():+.2f}R/trade, "
          f"second half {t.r.iloc[half:].mean():+.2f}R/trade  (stability check)")


if __name__ == "__main__":
    for s in ("SPY", "QQQ"):
        screen(s)
