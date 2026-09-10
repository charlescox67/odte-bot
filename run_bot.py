"""Poll Yahoo, evaluate the signal on a LAGGED clock, trade the paper book.

THE LAG IS THE WHOLE POINT. Yahoo gives SPY in real time but option quotes ~20
minutes stale. Signalling on live SPY and filling from the stale chain books
entries at pre-move prices — free money that does not exist. So the signal is
evaluated as of (now - LAG_MIN), which is the instant the chain is actually
quoting. The bot lives 20 minutes in the past and its P&L is honest.
"""
from __future__ import annotations

import argparse
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


def tick(book: pe.Book, *, verbose: bool = True) -> None:
    t_now = now_et()
    asof = t_now - timedelta(minutes=LAG_MIN)
    today = t_now.date().isoformat()
    spots: dict[str, float] = {}

    for sym in SYMBOLS:
        df = bars_1m(sym)
        if df.empty:
            print(f"{sym}: no bars"); continue
        spots[sym] = float(df["close"].iloc[-1])
        side, px_asof = signal(df, asof)

        # --- manage what is already open -------------------------------
        for pos in [p for p in book.open_positions if p.symbol == sym]:
            row, _ = chain_quote(sym, pos.expiry, pos.right, spots[sym])
            exact = None
            if row is not None:
                t = yf.Ticker(sym).option_chain(pos.expiry)
                tbl = t.calls if pos.right == "C" else t.puts
                hit = tbl[tbl.contractSymbol == pos.contract]
                if not hit.empty:
                    exact = hit.iloc[0]
            if exact is None:
                continue
            pos.mark = float(exact.bid)
            held = (t_now - datetime.fromisoformat(pos.entry_time).astimezone(ET))
            pct = pos.pnl_pct(pos.mark)
            reason = ("target" if pct >= TARGET else
                      "stop" if pct <= STOP else
                      "time_stop" if held >= timedelta(minutes=TIME_STOP_MIN) else
                      "eod" if t_now.time() >= FLATTEN_AT else None)
            if reason:
                book.close(pos, bid=pos.mark, reason=reason)
                print(f"  CLOSE {pos.contract} @ {pos.mark:.2f} ({reason}) "
                      f"pnl ${pos.pnl():+.2f} ({pct:+.0%})")

        # --- consider a new entry --------------------------------------
        if not side:
            if verbose:
                print(f"{sym}: no signal as of {asof:%H:%M} (px {px_asof:.2f})")
            continue
        if t_now.time() >= NO_ENTRY_AFTER:
            print(f"{sym}: signal {side} but past {NO_ENTRY_AFTER} cutoff"); continue
        if any(p.symbol == sym for p in book.open_positions):
            print(f"{sym}: signal {side} but already holding"); continue
        if any(p.symbol == sym and p.entry_time[:10] == today for p in book.positions):
            print(f"{sym}: signal {side} but already traded today"); continue

        row, strike = chain_quote(sym, today, side, px_asof)
        if row is None:
            print(f"{sym}: no 0DTE chain for {today}"); continue
        pos = book.open(symbol=sym, contract=row.contractSymbol, right=side,
                        strike=strike, expiry=today, qty=QTY, ask=float(row.ask),
                        underlying=px_asof, reason=f"ORB{OR_MIN}/{VOL_MULT}")
        print(f"  OPEN  {pos.contract} x{QTY} @ ask {pos.entry_price:.2f} "
              f"(bid {row.bid:.2f}, spread {row.ask-row.bid:.2f}) underlying {px_asof:.2f}")

    book.settle_expired(spots)
    book.save()
    print(f"equity ${book.equity():,.2f} | cash ${book.cash:,.2f} | "
          f"open {len(book.open_positions)} | closed {len(book.positions)-len(book.open_positions)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single tick then exit")
    a = ap.parse_args()
    book = pe.Book.load()
    print(f"--- tick {now_et():%Y-%m-%d %H:%M:%S %Z} (signals as of "
          f"{(now_et()-timedelta(minutes=LAG_MIN)):%H:%M}) ---")
    tick(book)
