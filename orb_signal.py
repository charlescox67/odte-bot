"""Does the opening-range breakout even fire, and does price follow through?

Underlying only. This measures SIGNAL QUALITY, not P&L — option P&L needs
historical chains that no free source has.
"""
from __future__ import annotations
import pandas as pd
from yahoo_data import bars

OR_BARS = 3        # first 15 minutes on 5m bars
VOL_MULT = 1.5     # "volume at 1.5x+ average"
HORIZON = 4        # 20 minutes after entry ~ the stated 15-20min time stop

df = bars("SPY", "60d", "5m")
days = [d for _, d in df.groupby(df.index.date) if len(d) > OR_BARS + HORIZON + 2]
print(f"trading days: {len(days)}  ({df.index[0].date()} .. {df.index[-1].date()})")

rows = []
for d in days:
    opening, rest = d.iloc[:OR_BARS], d.iloc[OR_BARS:]
    hi, lo = opening["high"].max(), opening["low"].min()
    avg_vol = opening["volume"].mean()
    for i in range(len(rest) - HORIZON):
        bar = rest.iloc[i]
        if bar["volume"] < VOL_MULT * avg_vol:
            continue
        if bar["close"] > hi:
            side = 1
        elif bar["close"] < lo:
            side = -1
        else:
            continue
        fwd = rest.iloc[i + 1 : i + 1 + HORIZON]
        entry = bar["close"]
        mfe = (fwd["high"].max() - entry) * side if side > 0 else (entry - fwd["low"].min())
        mae = (entry - fwd["low"].min()) * side if side > 0 else (fwd["high"].max() - entry)
        rows.append({"date": d.index[0].date(), "side": side, "entry": entry,
                     "mfe": mfe, "mae": mae,
                     "net": (fwd["close"].iloc[-1] - entry) * side,
                     "or_width": hi - lo})
        break   # max 1 signal/day, per the overtrading rule

s = pd.DataFrame(rows)
print(f"days with a signal: {len(s)} / {len(days)}  ({len(s)/len(days):.0%})")
print(f"long / short      : {(s.side==1).sum()} / {(s.side==-1).sum()}")
print(f"median OR width   : ${s.or_width.median():.2f}")
print(f"\nunderlying move in the {HORIZON*5} min after entry:")
print(f"  median MFE (best) : ${s.mfe.median():.2f}")
print(f"  median MAE (worst): ${s.mae.median():.2f}")
print(f"  median net        : ${s.net.median():+.2f}")
print(f"  net > 0           : {(s.net>0).sum()} / {len(s)}  ({(s.net>0).mean():.0%})")
print(f"  MFE > MAE         : {(s.mfe>s.mae).sum()} / {len(s)}  ({(s.mfe>s.mae).mean():.0%})")
