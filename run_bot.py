"""Poll Yahoo, evaluate the signal on a LAGGED clock, trade the paper book.

THE LAG IS THE WHOLE POINT. Yahoo gives SPY in real time but option quotes ~20
minutes stale. Signalling on live SPY and filling from the stale chain books
entries at pre-move prices — free money that does not exist. So the signal is
evaluated as of (now - LAG_MIN), which is the instant the chain is actually
quoting. The bot lives 20 minutes in the past and its P&L is honest.
"""
from __future__ import annotations

import argparse
import time
import warnings
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
import yfinance as yf

import paper_engine as pe

ET = ZoneInfo("America/New_York")
LAG_MIN = 20            # measured: Yahoo OPRA delay
SYMBOLS = ["SPY", "QQQ"]   # VOO excluded: no 0DTE, 15-40% spreads
OR_MIN = 15             # opening range
VOL_MULT = 1.25
TARGET, STOP = 1.00, -0.40      # +100% / -40% of premium
TIME_STOP_MIN = 20
NO_ENTRY_AFTER = dtime(14, 0)
FLATTEN_AT = dtime(15, 45)
QTY = 1


def now_et() -> datetime:
    return datetime.now(ET)


def retry(fn, tries: int = 3, label: str = ""):
    """yfinance scrapes an undocumented endpoint and drops out several times a
    session. A transient DNS blip must not look like 'no data'."""
    for i in range(tries):
        try:
            out = fn()
            if out is not None and not (hasattr(out, "empty") and out.empty):
                return out
        except Exception as e:
            if i == tries - 1:
                print(f"  ! {label} failed after {tries}: {type(e).__name__}: {e}")
        time.sleep(1.5 * (i + 1))
    return None


def spot_now(symbol: str) -> float | None:
    """Last price without needing the bar history — survives a bars outage."""
    def go():
        return yf.Ticker(symbol).fast_info["last_price"]
    v = retry(go, label=f"{symbol} spot")
    return float(v) if v else None


def close_on(symbol: str, day: str) -> float | None:
    """Underlying close on a specific past date, for settling a late expiry."""
    def go():
        d = yf.Ticker(symbol).history(start=day, end=day, interval="1d")
        return None if d.empty else float(d["Close"].iloc[0])
    return retry(go, label=f"{symbol} close {day}")


def bars_1m(symbol: str):
    df = yf.download(symbol, period="1d", interval="1m",
                     progress=False, auto_adjust=False)
    if df.empty:
        return df
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df.tz_convert(ET)


def signal(df, asof: datetime) -> tuple[str | None, float]:
    """Opening-range breakout evaluated strictly at or before `asof`."""
    d = df[df.index <= asof]
    if len(d) < OR_MIN + 2:
        return None, 0.0
    session = d[d.index.time >= dtime(9, 30)]
    if len(session) < OR_MIN + 2:
        return None, 0.0
    opening = session.iloc[:OR_MIN]
    hi, lo, avg = opening["high"].max(), opening["low"].min(), opening["volume"].mean()
    bar = session.iloc[-1]
    px = float(bar["close"])
    if bar["volume"] < VOL_MULT * avg:
        return None, px
    if px > hi:
        return "C", px
    if px < lo:
        return "P", px
    return None, px


def chain_quote(symbol: str, expiry: str, right: str, spot: float):
    """Nearest-the-money contract with a two-sided quote. Returns (row, strike)."""
    t = yf.Ticker(symbol)
    if expiry not in t.options:
        return None, None
    ch = t.option_chain(expiry)
    tbl = ch.calls if right == "C" else ch.puts
    tbl = tbl[(tbl.bid > 0) & (tbl.ask > 0)]
    if tbl.empty:
        return None, None
    row = tbl.iloc[(tbl.strike - spot).abs().argsort().iloc[0]]
    return row, float(row.strike)


def manage(book: pe.Book, sym: str, t_now: datetime) -> None:
    """Mark and exit open positions. Deliberately does NOT depend on the bar
    feed — a signal-data outage must never suspend stop-loss checking."""
    holding = [p for p in book.open_positions if p.symbol == sym]
    if not holding:
        return
    for expiry in {p.expiry for p in holding}:
        chain = retry(lambda: yf.Ticker(sym).option_chain(expiry),
                      label=f"{sym} chain {expiry}")
        if chain is None:
            print(f"  ! {sym} {expiry}: no chain, positions left open")
            continue
        for pos in [p for p in holding if p.expiry == expiry]:
            tbl = chain.calls if pos.right == "C" else chain.puts
            hit = tbl[tbl.contractSymbol == pos.contract]
            if hit.empty:
                continue
            pos.mark = float(hit.iloc[0].bid)
            held = t_now - datetime.fromisoformat(pos.entry_time).astimezone(ET)
            pct = pos.pnl_pct(pos.mark)
            reason = ("target" if pct >= TARGET else
                      "stop" if pct <= STOP else
                      "time_stop" if held >= timedelta(minutes=TIME_STOP_MIN) else
                      "eod" if t_now.time() >= FLATTEN_AT else None)
            if reason:
                book.close(pos, bid=pos.mark, reason=reason)
                print(f"  CLOSE {pos.contract} @ {pos.mark:.2f} ({reason}) "
                      f"pnl ${pos.pnl():+.2f} ({pct:+.0%})")


def consider_entry(book: pe.Book, sym: str, t_now: datetime, asof: datetime) -> None:
    today = t_now.date().isoformat()
    df = retry(lambda: bars_1m(sym), label=f"{sym} bars")
    if df is None:
        print(f"{sym}: no bars — entry skipped (positions still managed)")
        return
    side, px_asof = signal(df, asof)
    if not side:
        print(f"{sym}: no signal as of {asof:%H:%M} (px {px_asof:.2f})")
        return
    if t_now.time() >= NO_ENTRY_AFTER:
        print(f"{sym}: signal {side} but past {NO_ENTRY_AFTER} cutoff"); return
    if any(p.symbol == sym for p in book.open_positions):
        print(f"{sym}: signal {side} but already holding"); return
    if any(p.symbol == sym and p.entry_time[:10] == today for p in book.positions):
        print(f"{sym}: signal {side} but already traded today"); return

    row, strike = chain_quote(sym, today, side, px_asof)
    if row is None:
        print(f"{sym}: no 0DTE chain for {today}"); return
    pos = book.open(symbol=sym, contract=row.contractSymbol, right=side,
                    strike=strike, expiry=today, qty=QTY, ask=float(row.ask),
                    underlying=px_asof, reason=f"ORB{OR_MIN}/{VOL_MULT}")
    print(f"  OPEN  {pos.contract} x{QTY} @ ask {pos.entry_price:.2f} "
          f"(bid {row.bid:.2f}, spread {row.ask-row.bid:.2f}) underlying {px_asof:.2f}")


def settle_price(pos: pe.Position) -> float | None:
    """Price on the position's OWN expiry date, not today's."""
    if pos.expiry == now_et().date().isoformat():
        return spot_now(pos.symbol)
    return close_on(pos.symbol, pos.expiry)


def tick(book: pe.Book, *, verbose: bool = True) -> None:
    t_now = now_et()
    asof = t_now - timedelta(minutes=LAG_MIN)
    for sym in SYMBOLS:
        manage(book, sym, t_now)       # risk first, always
        consider_entry(book, sym, t_now, asof)
    book.settle_expired(settle_price)
    book.save()
    print(f"equity ${book.equity():,.2f} | cash ${book.cash:,.2f} | "
          f"open {len(book.open_positions)} | closed "
          f"{len(book.positions)-len(book.open_positions)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single tick then exit")
    a = ap.parse_args()
    book = pe.Book.load()
    print(f"--- tick {now_et():%Y-%m-%d %H:%M:%S %Z} (signals as of "
          f"{(now_et()-timedelta(minutes=LAG_MIN)):%H:%M}) ---")
    tick(book)
