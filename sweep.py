"""Is the MFE/MAE asymmetry a property of the strategy, or of one parameter pick?"""
from __future__ import annotations
import pandas as pd
from yahoo_data import bars

HORIZON = 4  # 20 min

df = bars("SPY", "60d", "5m")
by_day = [d for _, d in df.groupby(df.index.date)]

def run(or_bars: int, vol_mult: float) -> dict:
    rows = []
    for d in by_day:
        if len(d) < or_bars + HORIZON + 2:
            continue
        opening, rest = d.iloc[:or_bars], d.iloc[or_bars:]
        hi, lo, avg = opening["high"].max(), opening["low"].min(), opening["volume"].mean()
        for i in range(len(rest) - HORIZON):
            bar = rest.iloc[i]
            if bar["volume"] < vol_mult * avg:
                continue
            side = 1 if bar["close"] > hi else -1 if bar["close"] < lo else 0
            if not side:
                continue
            fwd, entry = rest.iloc[i + 1 : i + 1 + HORIZON], bar["close"]
            mfe = (fwd["high"].max() - entry) if side > 0 else (entry - fwd["low"].min())
            mae = (entry - fwd["low"].min()) if side > 0 else (fwd["high"].max() - entry)
            rows.append({"mfe": mfe, "mae": mae,
                         "net": (fwd["close"].iloc[-1] - entry) * side})
            break
    if not rows:
        return {"n": 0}
    s = pd.DataFrame(rows)
    return {"n": len(s), "win%": (s.net > 0).mean() * 100,
            "mfe": s.mfe.median(), "mae": s.mae.median(),
            "ratio": s.mfe.median() / s.mae.median(), "net": s.net.median()}

print(f"{'OR min':>7} {'volx':>5} {'n':>4} {'win%':>6} {'medMFE':>7} {'medMAE':>7} {'MFE/MAE':>8} {'medNet':>7}")
for or_bars in (1, 2, 3, 6):
    for vol_mult in (1.0, 1.25, 1.5, 2.0):
        r = run(or_bars, vol_mult)
        if not r["n"]:
            print(f"{or_bars*5:>7} {vol_mult:>5} {0:>4}   —")
            continue
        print(f"{or_bars*5:>7} {vol_mult:>5} {r['n']:>4} {r['win%']:>5.0f}% "
              f"{r['mfe']:>7.2f} {r['mae']:>7.2f} {r['ratio']:>8.2f} {r['net']:>+7.2f}")
