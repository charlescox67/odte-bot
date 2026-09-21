"""Deterministic checks on the paper book. No network, no market needed."""
from __future__ import annotations

import tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
AFTER_CLOSE = datetime(2026, 9, 10, 16, 1, tzinfo=ET)
from pathlib import Path

import paper_engine as pe

tmp = Path(tempfile.mkdtemp())
pe.BOOK, pe.TRADES = tmp / "book.json", tmp / "trades.csv"

ok = fail = 0
def check(label, got, want):
    global ok, fail
    good = (abs(got - want) < 1e-6) if isinstance(want, float) else (got == want)
    print(f"  {'PASS' if good else 'FAIL'}  {label}: got {got!r} want {want!r}")
    ok, fail = ok + good, fail + (not good)

print("open / cost / spread cost")
b = pe.Book(cash=10_000.0)
p = b.open(symbol="SPY", contract=pe.occ("SPY","2026-09-10","C",759.0), right="C",
           strike=759.0, expiry="2026-09-10", qty=2, ask=0.55,
           underlying=758.74, reason="test")
check("cash after buy 2 @ 0.55", b.cash, 10_000.0 - 110.0)
check("cost", p.cost, 110.0)

print("\nmark to market")
p.mark = 1.10
check("unrealised pnl at 1.10", p.pnl(), 110.0)
check("unrealised pct", round(p.pnl_pct(), 4), 1.0)
check("equity", b.equity(), 10_000.0 - 110.0 + 220.0)

print("\nclose at the BID, not the mid")
b.close(p, bid=1.05, reason="target")
check("cash after sell", b.cash, 10_000.0 - 110.0 + 210.0)
check("realised pnl", p.pnl(), 100.0)
check("status", p.status, "closed")
check("open positions", len(b.open_positions), 0)
check("trade logged", pe.TRADES.exists(), True)

print("\npersistence across a restart")
b.save(pe.BOOK)
b2 = pe.Book.load(pe.BOOK)
check("cash survives", b2.cash, b.cash)
check("history survives", len(b2.positions), 1)

print("\nexpiry settlement — a 0DTE has no next day")
b3 = pe.Book(cash=10_000.0)
itm = b3.open(symbol="SPY", contract="x", right="C", strike=750.0,
              expiry="2026-09-10", qty=1, ask=1.00, underlying=755.0, reason="t")
otm = b3.open(symbol="SPY", contract="y", right="C", strike=770.0,
              expiry="2026-09-10", qty=1, ask=0.20, underlying=755.0, reason="t")
# settlement now takes a callable so the caller can supply the price on the
# position's OWN expiry date rather than whatever spot happens to be now.

# Regression: every live trade 09-14..09-18 "expired" 0.3s after opening,
# mid-session, because a 0DTE's expiry date IS today. Settlement must wait
# for the 16:00 ET close, not just the calendar date.
b3.settle_expired(lambda pos: 756.30, now=datetime(2026, 9, 10, 11, 52, tzinfo=ET))
check("0DTE NOT settled mid-session", len(b3.open_positions), 2)
b3.settle_expired(lambda pos: 756.30, now=datetime(2026, 9, 10, 15, 59, tzinfo=ET))
check("0DTE NOT settled at 15:59", len(b3.open_positions), 2)
b3.settle_expired(lambda pos: {"SPY": 756.30}.get(pos.symbol), now=AFTER_CLOSE)
check("ITM settles to intrinsic 6.30", itm.exit_price, 6.30)
check("OTM expires worthless", otm.exit_price, 0.0)
check("OTM loses the full premium", otm.pnl(), -20.0)
check("both closed", len(b3.open_positions), 0)

print("\nunknown price must NOT settle at zero")
b4 = pe.Book(cash=10_000.0)
q = b4.open(symbol="SPY", contract="q", right="C", strike=750.0,
            expiry="2026-09-10", qty=1, ask=1.00, underlying=755.0, reason="t")
b4.settle_expired(lambda pos: None, now=AFTER_CLOSE)
check("stays open when price unknown", q.status, "open")

print("\nguards")
for label, fn in [
    ("refuse ask<=0", lambda: b3.open(symbol="SPY", contract="z", right="C",
        strike=1.0, expiry="2026-09-11", qty=1, ask=0.0, underlying=1.0, reason="t")),
    ("refuse over-spend", lambda: b3.open(symbol="SPY", contract="z", right="C",
        strike=1.0, expiry="2026-09-11", qty=99999, ask=5.0, underlying=1.0, reason="t")),
]:
    try:
        fn(); check(label, "no error", "ValueError")
    except ValueError:
        check(label, "ValueError", "ValueError")

print("\nOCC symbol format")
check("occ", pe.occ("SPY","2026-09-10","C",759.0), "SPY260910C00759000")

print("\nbackward compatibility")
import json
old = tmp / "old_book.json"
old.write_text(json.dumps({"cash": 5000.0, "positions": [{
    "id": "a", "symbol": "SPY", "contract": "c", "right": "C", "strike": 1.0,
    "expiry": "2026-09-10", "qty": 1, "entry_price": 1.0, "entry_time": "t",
    "entry_reason": "ORB15/1.25", "underlying_at_entry": 1.0}]}))
ob = pe.Book.load(old)
check("pre-swing book.json loads", ob.positions[0].stop_underlying, None)

pe.TRADES.write_text("id,symbol,old,columns\n1,SPY,x,y\n")
b5 = pe.Book(cash=10_000.0)
q5 = b5.open(symbol="QQQ", contract="q5", right="P", strike=700.0, expiry="2026-09-10",
             qty=1, ask=1.0, underlying=701.0, reason="swing", setup="H1005",
             stop_at_entry=702.0, stop_underlying=702.0, signal_symbol="QQQ")
b5.close(q5, bid=1.5, reason="swing_stop")
import csv as _csv
rows = list(_csv.reader(pe.TRADES.open()))
check("old-format trades.csv moved aside", len(list(tmp.glob("trades.old-*.csv"))), 1)
check("new file has new header", rows[0], pe.TRADE_COLUMNS)
check("context logged", rows[1][pe.TRADE_COLUMNS.index("setup")], "H1005")

print("\nlog upgrade keeps history (today's real first trade, pre-cost layout)")
pe.TRADES.write_text(
    "id,symbol,contract,right,strike,expiry,qty,entry_time,entry_price,underlying_at_entry,"
    "entry_reason,exit_time,exit_price,exit_reason,pnl,pnl_pct,signal_symbol,setup,"
    "stop_at_entry,stop_final,spread_at_entry,event_day\n"
    "ea4251b8,^SPX,SPXW260921C07735000,C,7735.0,2026-09-21,1,2026-09-21T16:05:01+00:00,"
    "10.5,7737.17,swing L1130,2026-09-21T17:06:01+00:00,18.2,time_stop,770.00,0.7333,"
    "SPY,L1130,769.81,770.52,0.1,\n")
before = len(list(tmp.glob("trades.old-*.csv")))
b6 = pe.Book(cash=10_000.0)
q6 = b6.open(symbol="QQQ", contract="q6", right="C", strike=738.0, expiry="2026-09-21",
             qty=10, ask=1.03, underlying=738.15, reason="swing", setup="L1135")
b6.close(q6, bid=1.20, reason="swing_stop")
rows = list(_csv.DictReader(pe.TRADES.open()))
check("upgraded in place, not moved aside", len(list(tmp.glob("trades.old-*.csv"))), before)
check("old row kept", rows[0]["id"], "ea4251b8")
check("old row cost = 10.50 x 1 x 100", rows[0]["cost"], "1050.00")
check("old row proceeds = 18.20 x 1 x 100", rows[0]["proceeds"], "1820.00")
check("new row cost = 1.03 x 10 x 100", rows[1]["cost"], "1030.00")
check("new row proceeds", rows[1]["proceeds"], "1200.00")
check("header now current", list(rows[0].keys()), pe.TRADE_COLUMNS)

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
