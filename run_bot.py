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

import event_calendar as ev
import paper_engine as pe
import swing_signal as sw

ET = ZoneInfo("America/New_York")
LAG_MIN = 20            # measured: Yahoo OPRA delay

# option underlying -> chart the signal reads. The SPX index reports no volume
# on Yahoo, so the S&P signal reads SPY. VOO is out: no 0DTE Mon-Thu and ~11%
# spreads, and it is the same S&P bet as SPX anyway.
# SPX is out as of 2026-09-21: one contract costs $500-2500, so a 0.5%-of-
# equity budget cannot buy one on a normal account, and the granularity gets
# worse as the account shrinks. SPY tracks the same index at ~1/10 the price.
MARKETS = {"SPY": "SPY", "QQQ": "QQQ"}

# Entry window on the LAGGED clock (the market time our fills are priced at).
ENTRY_START = dtime(10, 0)      # lets real pivots form after the open
NO_ENTRY_AFTER = dtime(14, 0)   # late-day gamma
# Everything is out by 14:45 on the lagged clock (the market time exits are
# priced at): the last hour is where 0DTE time decay is steepest.
FLATTEN_AT = dtime(14, 45)

RISK_PCT = 0.005                # of equity, lost if the backstop is hit
BACKSTOP = 0.50                 # exit if the option loses half its premium
MAX_QTY = 50                    # SPY/QQQ 0DTE trade thousands per minute
MAX_SPREAD = 0.10               # skip if bid-ask exceeds 10% of the ask
# Yahoo sometimes serves a gutted chain: on 2026-09-22 the QQQ 0DTE puts
# within $8 of spot listed ONE strike (750, with QQQ at 744), and the bot
# bought a deep in-the-money put. The nearest quoted strike must sit within
# 0.25% of the price (at least $1), or the entry waits for a complete chain.
MAX_STRIKE_GAP = 0.0025
# Entries are priced ~20 min in the past but stops watch the LIVE price, so a
# setup can already be failing by the time it is bought: on 2026-09-22 QQQ had
# used 34% of the room to the stop before the entry. Skip if half is gone.
MAX_USED_AT_ENTRY = 0.5
MAX_ENTRIES_PER_DAY = 3         # per market
DAILY_LOSS_LIMIT = 0.02         # of start-of-day equity: no new entries past it
TIME_STOP_MIN = 60
# ...but a trade that is working is handed to the trailing stop instead of
# being closed on the clock. Day one closed a +73% winner at 60 minutes that
# went on to +319%, and a flat trade that reached +374%. Over 60 days this
# lifts SPY from -0.14R to -0.05R per trade and leaves QQQ unchanged. The
# threshold is R = the distance from entry to the initial stop.
TIME_STOP_KEEP_R = 0.25
# Take the profit once the option is up 60%: twice the ~30% the stop risks.
# Checked on the (conservatively adjusted) option price, so a booked profit is
# one the delayed feed actually printed.
TARGET_PCT = 0.60
# ...unless the stock is breaking out HARD when the target is reached
# (swing_signal.breaking_out: >= 1R in 10 min, efficiency >= 0.5). Then the
# trade becomes a runner: held until a deep red candle (swing_signal.deep_dip)
# or until it falls back below +60%, so a runner never ends worse than the
# plain target would have. Slow grinds with ups and downs still sell at +60%.
# The chart stop must be close enough to fire BEFORE the premium backstop. A
# stop 1.0 away on a $5.60 option cost ~90% of it, so the backstop always won
# and the swing stop never acted. Cap the distance at what 30% of premium buys.
STOP_COST_CAP = 0.30


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


def et_date(iso: str):
    return datetime.fromisoformat(iso).astimezone(ET).date()


def size_qty(equity: float, ask: float, risk_mult: float = 1.0) -> int:
    """Contracts such that hitting the 50% backstop loses RISK_PCT of equity.
    0 means the contract is too expensive for the risk budget: skip it."""
    per_contract = ask * pe.MULTIPLIER * BACKSTOP
    if per_contract <= 0:
        return 0
    return min(MAX_QTY, int(equity * RISK_PCT * risk_mult // per_contract))


def day_pnl(book: pe.Book, day) -> float:
    """Realised plus open P&L of positions entered on `day` (ET)."""
    return sum(p.pnl() for p in book.positions if et_date(p.entry_time) == day)


def loss_limit_hit(book: pe.Book, day) -> bool:
    dp = day_pnl(book, day)
    return dp <= -DAILY_LOSS_LIMIT * (book.equity() - dp)


def est_delta(tbl, strike: float, right: str) -> float:
    """Delta straight from the chain: how much the option mid moves per $1 of
    strike. No pricing model, no volatility guess. Falls back to a plain ATM
    0.5 when neighbouring strikes are too far apart to differentiate."""
    t = tbl[(tbl.bid > 0) & (tbl.ask > 0)].sort_values("strike").reset_index(drop=True)
    i = int((t.strike - strike).abs().idxmin())
    lo, hi = t.iloc[max(0, i - 1)], t.iloc[min(len(t) - 1, i + 1)]
    width = hi.strike - lo.strike
    sign = 1.0 if right == "C" else -1.0
    if width <= 0 or width > 3:
        return sign * 0.5
    mid = lambda r: (r.bid + r.ask) / 2
    d = -(mid(hi) - mid(lo)) / width          # calls cheapen as strike rises
    return sign * min(0.95, max(0.15, abs(d)))


def decide_exit(*, pct: float, stop_broken: bool, aged: bool,
                r_now: float | None, asof_t, event_flatten=None,
                runner: bool = False, strong: bool = False,
                dip: bool = False) -> str | None:
    """Why to close, in priority order, or None to hold.
    Losses first (never hold something tanking), then profit, then decay.
    Returns "runner" to mean: don't sell at the target, switch to runner mode."""
    if pct <= -BACKSTOP:
        return "backstop"
    if stop_broken:
        return "swing_stop"
    if runner:
        if dip:
            return "runner_dip"     # the deep red candle
        if pct < TARGET_PCT:
            return "runner_floor"   # never finish worse than the plain target
    elif pct >= TARGET_PCT:
        return "runner" if strong else "target"
    if not runner and aged and (r_now is None or r_now < TIME_STOP_KEEP_R):
        return "time_stop"          # going nowhere while the premium decays
    if event_flatten and asof_t >= event_flatten:
        return "event_flatten"
    if asof_t >= FLATTEN_AT:
        return "eod"
    return None


def progress_r(pos, live_px: float | None) -> float | None:
    """How far the trade has come, in units of its own initial risk."""
    if live_px is None or pos.stop_at_entry is None:
        return None
    risk = abs(pos.underlying_at_entry - pos.stop_at_entry)
    if risk <= 0:
        return None
    sign = 1.0 if pos.right == "C" else -1.0
    return sign * (live_px - pos.underlying_at_entry) / risk


def room_used(side: str, entry_px: float, stop: float, live_px: float | None) -> float:
    """Fraction of the entry-to-stop distance already lost on LIVE prices.
    0 = untouched (or moved in our favour), 1+ = the stop is already broken."""
    risk = abs(entry_px - stop)
    if live_px is None or risk <= 0:
        return 0.0
    against = (entry_px - live_px) if side == "C" else (live_px - entry_px)
    return max(0.0, against / risk)


def cap_stop(side: str, ref: float, pivot: float, ask: float, delta: float) -> float:
    """Pull the stop in so reaching it costs at most STOP_COST_CAP of premium."""
    max_dist = STOP_COST_CAP * ask / abs(delta)
    return max(pivot, ref - max_dist) if side == "C" else min(pivot, ref + max_dist)


def live_price(sig_bars, t_now: datetime) -> float | None:
    """Underlying now. SPY/QQQ bars are real time; only option quotes lag."""
    d = sw.complete_1m(sig_bars, t_now)
    return None if d is None or d.empty else float(d["close"].iloc[-1])


def adjust_mark(bid: float, delta: float | None,
                live_px: float | None, lagged_px: float | None) -> float:
    """Estimate what the option is worth NOW from a ~16-20 minute old quote
    plus the underlying move since it was taken.

    Only ever marks DOWN. A favourable move keeps the stale quote, so the book
    never books a gain the delayed feed has not actually printed; an adverse
    move is recognised immediately, which is the point of using live data."""
    if delta is None or live_px is None or lagged_px is None:
        return bid
    return max(0.0, min(bid, bid + delta * (live_px - lagged_px)))


def chain_quote(symbol: str, expiry: str, right: str, spot: float):
    """Nearest-the-money contract with a two-sided quote.
    Returns (row, strike, table) — the table is kept for the delta estimate."""
    t = yf.Ticker(symbol)
    if expiry not in t.options:
        return None, None, None
    ch = t.option_chain(expiry)
    tbl = ch.calls if right == "C" else ch.puts
    tbl = tbl[(tbl.bid > 0) & (tbl.ask > 0)]
    if tbl.empty:
        return None, None, None
    row = tbl.iloc[(tbl.strike - spot).abs().argsort().iloc[0]]
    if abs(row.strike - spot) > max(1.0, MAX_STRIKE_GAP * spot):
        print(f"  ! {symbol} chain incomplete: nearest quoted strike {row.strike:g} "
              f"is {abs(row.strike - spot):.2f} from {spot:.2f}")
        return None, None, None
    return row, float(row.strike), tbl


def manage(book: pe.Book, sym: str, t_now: datetime, asof: datetime,
           sig_bars, profile: ev.DayProfile) -> None:
    """Mark and exit open positions. The premium backstop needs only the
    option chain, so it runs even when the chart feed is down; the swing
    stop and its trailing need the chart and run whenever it is available."""
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
            quoted = float(hit.iloc[0].bid)
            live_px = live_price(sig_bars, t_now) if sig_bars is not None else None
            lagged_px = live_price(sig_bars, asof) if sig_bars is not None else None
            delta = est_delta(tbl, pos.strike, pos.right)
            pos.mark = adjust_mark(quoted, delta, live_px, lagged_px)
            pos.exit_basis = "estimated" if pos.mark < quoted else "quote"
            held = t_now - datetime.fromisoformat(pos.entry_time).astimezone(ET)
            pct = pos.pnl_pct(pos.mark)
            stop_broken = False
            if sig_bars is not None and pos.stop_underlying is not None:
                new = sw.trail_stop(sig_bars, asof, pos.right, pos.stop_underlying)
                if new != pos.stop_underlying:
                    print(f"  TRAIL {pos.contract} stop {pos.stop_underlying:.2f} -> {new:.2f}")
                    pos.stop_underlying = new
                # Checked on the LIVE price, not the lagged one: the chart is
                # real time, so a break is acted on now rather than 20 min late.
                stop_broken = live_px is not None and (
                    live_px < pos.stop_underlying if pos.right == "C"
                    else live_px > pos.stop_underlying)
            risk = (abs(pos.underlying_at_entry - pos.stop_at_entry)
                    if pos.stop_at_entry is not None else 0.0)
            live = sig_bars is not None
            reason = decide_exit(
                pct=pct, stop_broken=stop_broken,
                aged=held >= timedelta(minutes=TIME_STOP_MIN),
                r_now=progress_r(pos, live_px), asof_t=asof.time(),
                event_flatten=profile.flatten_at, runner=pos.runner,
                # both read the LIVE candles: the move is happening now
                strong=live and sw.breaking_out(sig_bars, t_now, pos.right, risk),
                dip=live and pos.runner and sw.deep_dip(sig_bars, t_now, pos.right))
            if reason == "runner":
                pos.runner = True
                print(f"  RUNNER {pos.contract} at {pct:+.0%}: breaking out hard, "
                      f"holding for a deep red candle (floor +{TARGET_PCT:.0%})")
                reason = None
            if reason:
                book.close(pos, bid=pos.mark, reason=reason)
                print(f"  CLOSE {pos.contract} x{pos.qty} @ {pos.mark:.2f} ({reason}) "
                      f"pnl ${pos.pnl():+.2f} ({pct:+.0%})")


def consider_entry(book: pe.Book, sym: str, sig_sym: str, sig_bars,
                   t_now: datetime, asof: datetime, profile: ev.DayProfile) -> None:
    if sig_bars is None:
        print(f"{sym}: no {sig_sym} bars — entry skipped (positions still managed)")
        return
    today = asof.date()
    mine_today = [p for p in book.positions
                  if p.symbol == sym and et_date(p.entry_time) == today]
    setup = sw.swing_setup(sig_bars, asof, {p.setup for p in mine_today if p.setup})
    if not setup:
        print(f"{sym}: no setup as of {asof:%H:%M} | {sw.summary(sig_bars, asof)}")
        return
    tag = f"{sym}: {setup.side} setup {setup.pivot_id} stop {setup.stop:.2f} ({sig_sym})"
    cutoff = min(NO_ENTRY_AFTER, profile.entry_cutoff or NO_ENTRY_AFTER)
    if not ENTRY_START <= asof.time() < cutoff:
        print(f"{tag} — outside entry window {ENTRY_START:%H:%M}-{cutoff:%H:%M}"); return
    if any(p.symbol == sym for p in book.open_positions):
        print(f"{tag} — already holding"); return
    if len(mine_today) >= MAX_ENTRIES_PER_DAY:
        print(f"{tag} — {MAX_ENTRIES_PER_DAY} entries already today"); return
    if loss_limit_hit(book, today):
        print(f"{tag} — daily loss limit hit, no new entries today"); return

    # Strike comes from the option underlying's OWN price at asof (SPX, not SPY).
    if sym == sig_sym:
        ref = setup.price
    else:
        ub = retry(lambda: bars_1m(sym), label=f"{sym} bars")
        d = sw.complete_1m(ub, asof) if ub is not None else None
        if d is None or d.empty:
            print(f"{tag} — no {sym} price at {asof:%H:%M}"); return
        ref = float(d["close"].iloc[-1])
    row, strike, tbl = chain_quote(sym, t_now.date().isoformat(), setup.side, ref)
    if row is None:
        print(f"{tag} — no 0DTE chain"); return
    ask, bid = float(row.ask), float(row.bid)
    if ask - bid > MAX_SPREAD * ask:
        print(f"{tag} — spread {bid:.2f}/{ask:.2f} over {MAX_SPREAD:.0%}"); return
    # Pull the stop in if the pivot sits further away than 30% of premium
    # buys: otherwise the backstop always fires first and the chart stop is
    # decoration (that is how one trade lost 50% with price above its stop).
    delta = est_delta(tbl, strike, setup.side)
    stop = cap_stop(setup.side, ref, setup.stop, ask, delta)
    if abs(stop - setup.stop) > 1e-9:
        print(f"{tag} — stop pulled in to {stop:.2f} "
              f"(pivot {setup.stop:.2f} would cost ~{abs(ref - setup.stop) * abs(delta) / ask:.0%})")
    used = room_used(setup.side, ref, stop, live_price(sig_bars, t_now))
    if used >= MAX_USED_AT_ENTRY:
        print(f"{tag} — already failing on live prices ({used:.0%} of the room "
              f"to the stop used since the signal)"); return
    qty = size_qty(book.equity(), ask, profile.risk_mult)
    if qty < 1:
        print(f"{tag} — ask {ask:.2f} too expensive for the risk budget"); return
    pos = book.open(symbol=sym, contract=row.contractSymbol, right=setup.side,
                    strike=strike, expiry=t_now.date().isoformat(), qty=qty, ask=ask,
                    underlying=ref, reason=f"swing {setup.pivot_id}",
                    signal_symbol=sig_sym, setup=setup.pivot_id,
                    stop_at_entry=stop, stop_underlying=stop,
                    spread_at_entry=round(ask - bid, 4), event_day=profile.label)
    print(f"  OPEN  {pos.contract} x{qty} @ ask {ask:.2f} (bid {bid:.2f}) "
          f"{sym} {ref:.2f} | stop {stop:.2f} (delta {delta:+.2f})"
          f"{' | ' + profile.label if profile.label else ''}")


def settle_price(pos: pe.Position) -> float | None:
    """Price on the position's OWN expiry date, not today's."""
    if pos.expiry == now_et().date().isoformat():
        return spot_now(pos.symbol)
    return close_on(pos.symbol, pos.expiry)


def tick(book: pe.Book, *, verbose: bool = True) -> None:
    t_now = now_et()
    asof = t_now - timedelta(minutes=LAG_MIN)
    profile = ev.day_profile(asof.date())
    if not profile.covered:
        print(f"!!! event_calendar.py ends {ev.COVERED_THROUGH} — add new dates")
    if profile.label:
        print(f"event day: {profile.label} (risk x{profile.risk_mult})")
    bars = {s: retry(lambda s=s: bars_1m(s), label=f"{s} bars")
            for s in dict.fromkeys(MARKETS.values())}
    for sym, sig in MARKETS.items():
        manage(book, sym, t_now, asof, bars[sig], profile)   # risk first, always
        consider_entry(book, sym, sig, bars[sig], t_now, asof, profile)
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
