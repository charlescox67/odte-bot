"""Deterministic checks on the swing signal, sizing and event rules. No network."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import event_calendar as ev
import paper_engine as pe
import run_bot as rb
import swing_signal as sw

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 9, 22, 9, 30, tzinfo=ET)

ok = fail = 0
def check(label, got, want):
    global ok, fail
    good = (abs(got - want) < 1e-6) if isinstance(want, float) else (got == want)
    print(f"  {'PASS' if good else 'FAIL'}  {label}: got {got!r} want {want!r}")
    ok, fail = ok + good, fail + (not good)


def bars(five_min: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """1-minute bars whose 5-minute aggregation is exactly the given OHLC."""
    rows, idx = [], []
    for n, (o, h, l, c) in enumerate(five_min):
        t0 = DAY + timedelta(minutes=5 * n)
        for m, bar in enumerate([(o, h, o, o), (o, o, l, o), (o, o, o, o), (o, o, o, o),
                                 (o, max(o, c), min(o, c), c)]):
            idx.append(t0 + timedelta(minutes=m)); rows.append((*bar, 1000))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx),
                        columns=["open", "high", "low", "close", "volume"])


def after(n_bars: int, secs: int = 30) -> datetime:
    """asof just after the n-th 5-minute bar has completed."""
    return DAY + timedelta(minutes=5 * n_bars, seconds=secs)


# Uptrend: swing low A (b3, 100.1), higher swing low B (b7, 100.9, 10:05),
# B confirmed at b9, and b9 closes above b8's high -> call.
UP = [(100.0, 100.5, 99.8, 100.4), (100.4, 101.0, 100.2, 100.9),
      (100.9, 101.2, 100.5, 100.6), (100.6, 100.8, 100.1, 100.3),
      (100.3, 101.3, 100.3, 101.2), (101.2, 101.8, 101.0, 101.7),
      (101.7, 102.0, 101.3, 101.4), (101.4, 101.6, 100.9, 101.1),
      (101.1, 101.5, 101.0, 101.3), (101.3, 102.2, 101.2, 102.1)]

print("uptrend pullback")
s = sw.swing_setup(bars(UP), after(10))
check("call setup", s and s.side, "C")
check("stop = the higher low", s and s.stop, 100.9)
check("pivot id", s and s.pivot_id, "L1005")
check("price = last completed 1m close", s and s.price, 102.1)

print("\nno look-ahead")
check("b9 still forming -> nothing", sw.swing_setup(bars(UP), after(10) - timedelta(seconds=60)), None)
spike = sw.swing_setup(bars(UP + [(150.0, 150.0, 150.0, 150.0)]), after(10))
check("forming 10:20 1m bar (150) doesn't leak into price", spike and spike.price, 102.1)

print("\nfilters")
lower_low = list(UP); lower_low[7] = (101.4, 101.6, 99.9, 100.2)
check("lower low -> no call", sw.swing_setup(bars(lower_low), after(10)), None)
check("pivot already traded", sw.swing_setup(bars(UP), after(10), {"L1005"}), None)
stale = list(UP); c = 102.1
for _ in range(6):
    stale.append((c, c + 0.4, c - 0.05, c + 0.3)); c += 0.3
check("pivot confirmed 6+ bars ago -> stale", sw.swing_setup(bars(stale), after(len(stale))), None)
check("same run 1 bar later is still fresh", (sw.swing_setup(bars(stale[:11]), after(11)) or sw.Setup("", 0, 0, "")).pivot_id, "L1005")

print("\ndowntrend mirror")
DOWN = [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in UP]
d = sw.swing_setup(bars(DOWN), after(10))
check("put setup", d and d.side, "P")
check("stop = the lower high", d and d.stop, 99.1)
check("pivot id", d and d.pivot_id, "H1005")

print("\ntrailing stop")
RUN = UP + [(102.1, 102.6, 102.0, 102.5), (102.5, 102.9, 102.3, 102.4),
            (102.4, 102.5, 101.9, 102.0), (102.0, 102.7, 102.0, 102.6),
            (102.6, 103.0, 102.4, 102.9)]
df = bars(RUN)
check("ratchets up to new swing low", sw.trail_stop(df, after(15), "C", 100.9), 101.9)
check("never loosens", sw.trail_stop(df, after(15), "C", 102.5), 102.5)
check("not hit while price above", sw.stop_hit(df, after(15), "C", 101.9), False)
check("hit once close is below", sw.stop_hit(df, after(15), "C", 103.0), True)

print("\nsizing: 0.5% of equity lost at the 50% backstop, any account size")
check("SPY ask 0.42 on $100k", rb.size_qty(100_000, 0.42), 23)
check("same rule on a $20k account", rb.size_qty(20_000, 0.42), 4)
check("cheap contract hits the cap", rb.size_qty(100_000, 0.10), rb.MAX_QTY)
check("too expensive for the budget", rb.size_qty(100_000, 25.0), 0)
check("half risk on data days", rb.size_qty(100_000, 0.42, 0.5), 11)

print("\ndelta straight from the chain")
tbl = pd.DataFrame({"strike": [770.0, 771.0, 772.0, 773.0],
                    "bid": [3.00, 2.20, 1.50, 0.95], "ask": [3.10, 2.30, 1.60, 1.05]})
check("ATM call delta from neighbours", round(rb.est_delta(tbl, 771.0, "C"), 2), 0.75)
check("put delta is negative", rb.est_delta(tbl, 771.0, "P") < 0, True)
wide = pd.DataFrame({"strike": [740.0, 745.0], "bid": [3.0, 0.5], "ask": [3.1, 0.6]})
check("strikes too far apart -> plain 0.5", rb.est_delta(wide, 741.0, "C"), 0.5)

print("\nstop must be reachable before the backstop")
# The -$840 trade in SPY terms: a 0DTE call at 0.42 with the pivot 1.00 away.
# Reaching that pivot would cost 0.50 = 119% of the premium, so it is useless
# as a stop: the backstop always fires first. Pulled in to what 30% buys.
far = rb.cap_stop("C", 772.80, 771.80, 0.42, 0.50)
check("far pivot pulled in", round(far, 3), 772.548)
check("cost at the new stop is 30%", round((772.80 - far) * 0.50 / 0.42, 2), 0.30)
check("a close pivot is left alone", rb.cap_stop("C", 772.80, 772.60, 1.50, 0.50), 772.60)
check("richer premium allows a wider stop", round(rb.cap_stop("C", 772.80, 770.00, 1.50, 0.50), 2), 771.90)
check("puts mirror", round(rb.cap_stop("P", 772.80, 774.00, 0.42, -0.50), 3), 773.052)

print("\nlive marking never invents a gain")
check("adverse move marks down now", rb.adjust_mark(5.00, 0.50, 771.0, 772.0), 4.50)
check("favourable move keeps the stale quote", rb.adjust_mark(5.00, 0.50, 773.0, 772.0), 5.00)
check("put gains as price falls -> still stale", rb.adjust_mark(5.00, -0.50, 771.0, 772.0), 5.00)
check("put marks down as price rises", rb.adjust_mark(5.00, -0.50, 773.0, 772.0), 4.50)
check("never below zero", rb.adjust_mark(0.20, 0.50, 760.0, 772.0), 0.0)
check("no data -> unchanged", rb.adjust_mark(5.00, None, None, 772.0), 5.00)

print("\nthe clock only closes trades going nowhere")
class FakePos:
    right, underlying_at_entry, stop_at_entry = "C", 100.0, 99.0   # 1R = 1.00
check("flat trade", rb.progress_r(FakePos(), 100.0), 0.0)
check("up half its risk", rb.progress_r(FakePos(), 100.5), 0.5)
check("below entry", rb.progress_r(FakePos(), 99.5), -0.5)
class FakePut(FakePos):
    right, stop_at_entry = "P", 101.0
check("puts measure downward", rb.progress_r(FakePut(), 99.0), 1.0)
class NoStop(FakePos):
    stop_at_entry = None
check("no stop recorded -> unknown", rb.progress_r(NoStop(), 100.0), None)
check("no live price -> unknown", rb.progress_r(FakePos(), None), None)
check("a working trade is held", 0.30 >= rb.TIME_STOP_KEEP_R, True)
check("a dead trade is closed", 0.10 < rb.TIME_STOP_KEEP_R, True)

print("\nexit rules, in priority order")
from datetime import time as T
E = lambda **k: rb.decide_exit(**{**dict(pct=0.0, stop_broken=False, aged=False,
                                         r_now=0.0, asof_t=T(11, 0)), **k})
check("nothing to do -> hold", E(), None)
check("tanking: stop broken -> sell", E(stop_broken=True), "swing_stop")
check("down 50% -> backstop", E(pct=-0.50), "backstop")
check("up 60% -> take the profit", E(pct=0.60), "target")
check("up 59% -> keep holding", E(pct=0.59), None)
check("tanking beats everything else", E(pct=0.70, stop_broken=True), "swing_stop")
check("60m and going nowhere -> close (decay)", E(aged=True, r_now=0.10), "time_stop")
check("60m but working -> hold", E(aged=True, r_now=0.40), None)
check("14:45 -> out, however it's doing", E(asof_t=T(14, 45), r_now=0.9, pct=0.3), "eod")
check("14:44 -> still holding", E(asof_t=T(14, 44), r_now=0.9, pct=0.3), None)
check("FOMC early flatten", E(asof_t=T(13, 55), event_flatten=T(13, 55)), "event_flatten")

print("\nhard breakout vs slow grind")
def one_min(closes, opens=None):
    idx = pd.DatetimeIndex([DAY + timedelta(minutes=i) for i in range(len(closes))])
    o = opens or [closes[0]] + closes[:-1]
    return pd.DataFrame({"open": o, "high": [max(a, b) for a, b in zip(o, closes)],
                         "low": [min(a, b) for a, b in zip(o, closes)],
                         "close": closes, "volume": 1000}, index=idx)
end = lambda df: df.index[-1] + timedelta(minutes=1)
straight = one_min([100 + 0.1 * i for i in range(25)])           # +1.0 in 10 min, no dips
check("straight line = efficiency 1.0", round(sw.efficiency(straight, end(straight)), 2), 1.0)
check("fast and clean -> breaking out", sw.breaking_out(straight, end(straight), "C", 0.5), True)
check("same move vs a wide stop -> just a grind", sw.breaking_out(straight, end(straight), "C", 2.0), False)
check("puts need the move DOWN", sw.breaking_out(straight, end(straight), "P", 0.5), False)
zig = one_min([100 + (0.3 if i % 2 else 0) + 0.02 * i for i in range(25)])  # ups and downs
check("ups and downs = low efficiency", sw.efficiency(zig, end(zig)) < 0.5, True)
check("choppy rise -> not a breakout", sw.breaking_out(zig, end(zig), "C", 0.1), False)

calm = [100 + (0.05 if i % 2 else 0) for i in range(22)]
dip = one_min(calm + [99.75], None)                                # red body 0.30 vs typical 0.05
check("deep red candle -> dip", sw.deep_dip(dip, end(dip), "C"), True)
small = one_min(calm + [99.95])
check("ordinary red candle -> no dip", sw.deep_dip(small, end(small), "C"), False)
check("for puts, a deep RED candle is good news", sw.deep_dip(dip, end(dip), "P"), False)
pop = one_min(calm + [100.35])
check("for puts, a deep GREEN candle is the dip", sw.deep_dip(pop, end(pop), "P"), True)

print("\nrunner mode")
check("+60% on a slow grind -> take the profit", E(pct=0.60, strong=False), "target")
check("+60% breaking out hard -> become a runner", E(pct=0.60, strong=True), "runner")
check("runner keeps going above +60%", E(pct=1.50, runner=True), None)
check("runner: deep red candle -> sell", E(pct=1.50, runner=True, dip=True), "runner_dip")
check("runner: falls back under +60% -> sell", E(pct=0.55, runner=True), "runner_floor")
check("runner ignores the decay clock", E(pct=0.90, runner=True, aged=True, r_now=0.1), None)
check("runner still obeys the stop", E(pct=0.90, runner=True, stop_broken=True), "swing_stop")
check("runner still out at 14:45", E(pct=2.0, runner=True, asof_t=T(14, 45)), "eod")

print("\nincomplete chains are refused")
class FakeTicker:
    def __init__(self, strikes): self.strikes, self.options = strikes, ("2026-09-22",)
    def option_chain(self, exp):
        t = pd.DataFrame({"strike": self.strikes, "bid": 1.0, "ask": 1.05,
                          "contractSymbol": [f"Q{k:g}" for k in self.strikes]})
        return type("C", (), {"calls": t, "puts": t})()
real_ticker = rb.yf.Ticker
rb.yf.Ticker = lambda sym: FakeTicker([750.0])                       # the 09-22 gutted chain
row, k, _ = rb.chain_quote("QQQ", "2026-09-22", "P", 744.29)
check("only a strike $5.71 away -> skip", row, None)
rb.yf.Ticker = lambda sym: FakeTicker([743.0, 744.0, 745.0, 750.0])
row, k, _ = rb.chain_quote("QQQ", "2026-09-22", "P", 744.29)
check("complete chain -> nearest strike", k, 744.0)
rb.yf.Ticker = real_ticker

print("\nno buying a setup that is already failing on live prices")
check("09-22 QQQ put: 34% used at entry", round(rb.room_used("P", 744.29, 745.15, 744.58), 2), 0.34)
check("34% -> still allowed", rb.room_used("P", 744.29, 745.15, 744.58) < rb.MAX_USED_AT_ENTRY, True)
check("halfway to the stop -> skip", rb.room_used("P", 744.29, 745.15, 744.72) >= rb.MAX_USED_AT_ENTRY, True)
check("already through the stop -> skip", rb.room_used("P", 744.29, 745.15, 745.40) >= 1.0, True)
check("moved in our favour -> 0 used", rb.room_used("P", 744.29, 745.15, 743.90), 0.0)
check("calls mirror", round(rb.room_used("C", 100.0, 99.0, 99.6), 2), 0.4)
check("no live price -> no judgement", rb.room_used("C", 100.0, 99.0, None), 0.0)

print("\ndaily loss limit")
import tempfile
from pathlib import Path
pe.TRADES = Path(tempfile.mkdtemp()) / "trades.csv"
bk = pe.Book(cash=100_000.0)
ts = datetime(2026, 9, 22, 11, 0, tzinfo=ET)
x = bk.open(symbol="QQQ", contract="x", right="C", strike=1.0, expiry="2026-09-22",
            qty=10, ask=1.5, underlying=1.0, reason="t", ts=ts)
bk.close(x, bid=0.1, reason="t", ts=ts)   # -1,400
check("-1.4% -> still allowed", rb.loss_limit_hit(bk, ts.date()), False)
y = bk.open(symbol="QQQ", contract="y", right="C", strike=1.0, expiry="2026-09-22",
            qty=10, ask=1.0, underlying=1.0, reason="t", ts=ts)
bk.close(y, bid=0.3, reason="t", ts=ts)   # -700 -> -2,100 total
check("-2.1% -> blocked", rb.loss_limit_hit(bk, ts.date()), True)
check("yesterday's losses don't count", rb.loss_limit_hit(bk, date(2026, 9, 23)), False)

print("\nevent calendar")
f = ev.day_profile(date(2026, 10, 28))
check("FOMC label", f.label, "FOMC")
check("FOMC entry cutoff", f.entry_cutoff, time(13, 0))
check("FOMC flatten", f.flatten_at, time(13, 55))
check("CPI day half risk", ev.day_profile(date(2026, 10, 14)).risk_mult, 0.5)
check("ordinary day", ev.day_profile(date(2026, 9, 22)).label, "")
check("2027 flagged uncovered", ev.day_profile(date(2027, 1, 5)).covered, False)

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
