"""Live market data from IBKR through a running TWS.

Everything the bot needs — 1-minute bars, the 0DTE chain, bid/ask and delta —
read from the real feed instead of Yahoo's ~16-minute-stale option quotes.
`lag_min = 0` is the whole point: with live quotes the signal clock and the
fill clock are the same clock, so none of the lag compensation in run_bot
(chain_ref_price, adjust_mark, shifted_bid) has anything to correct.

Requires TWS running on 127.0.0.1:7497 with the API enabled, a live market
data subscription, and that subscription shared with the paper account.

TRAP: shared market data feeds ONE session at a time. Logging into the live
Client Portal or the IBKR phone app takes the feed away from TWS and every
request here starts failing with error 162, "Trading TWS session is connected
from a different IP address".
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from ib_async import Index, Option, Stock

import connect
from market_data import Quote, occ_symbol

ET = ZoneInfo("America/New_York")

# How long to wait for a streaming subscription to deliver. Quotes arrive in
# well under a second on a live feed; greeks are computed server-side and take
# longer. Both are bounded so a dead feed cannot stall a 60-second tick.
QUOTE_WAIT = 4.0
GREEK_WAIT = 3.0
# How far either side of spot to look when picking the near-the-money strike.
STRIKE_WINDOW = 5.0


def _is_live(t) -> bool:
    """1 = live, 2 = frozen (last snapshot before the close), 3/4 = delayed.
    Frozen is fine: outside RTH it is the correct answer and the bot does not
    trade then. Delayed is not — it would reintroduce the lag with none of the
    compensation, silently."""
    return t.marketDataType in (0, 1, 2)


class IBKRSource:
    """Live data. Construct once per process; call close() when done."""

    lag_min = 0
    name = "IBKR"

    def __init__(self, client_id: int | None = None):
        self.ib = (connect.connect(client_id=client_id) if client_id
                   else connect.connect())
        self.ib.reqMarketDataType(1)          # live; TWS falls back on its own
        self._contracts: dict = {}            # symbol -> qualified underlying
        self._chains: dict = {}               # symbol -> (expirations, strikes)

    def close(self) -> None:
        try:
            self.ib.disconnect()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- bars

    def _underlying(self, symbol: str):
        if symbol not in self._contracts:
            c = Index(symbol[1:], "CBOE", "USD") if symbol.startswith("^") \
                else Stock(symbol, "SMART", "USD")
            if not self.ib.qualifyContracts(c):
                raise RuntimeError(f"{symbol}: could not qualify contract")
            self._contracts[symbol] = c
        return self._contracts[symbol]

    def bars_1m(self, symbol: str, period: str = "1d"):
        """Same shape as run_bot.bars_1m: ET-indexed open/high/low/close/volume,
        regular trading hours only."""
        days = int(period.rstrip("dD") or 1)
        bars = self.ib.reqHistoricalData(
            self._underlying(symbol), endDateTime="", durationStr=f"{days} D",
            barSizeSetting="1 min", whatToShow="TRADES", useRTH=True,
            formatDate=1, timeout=20)
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame(
            [{"open": b.open, "high": b.high, "low": b.low,
              "close": b.close, "volume": b.volume} for b in bars],
            index=pd.DatetimeIndex([b.date for b in bars]))
        return df.tz_convert(ET) if df.index.tz else df.tz_localize(ET)

    # -------------------------------------------------------------- quotes

    def _snap(self, contract, greeks: bool = False):
        """Subscribe, wait for a two-sided quote, unsubscribe."""
        t = self.ib.reqMktData(contract, "", False, False)
        try:
            waited = 0.0
            while waited < QUOTE_WAIT and not (t.bid == t.bid and t.ask == t.ask):
                self.ib.sleep(0.25)
                waited += 0.25
            if greeks:
                g = 0.0
                while g < GREEK_WAIT and (t.modelGreeks is None
                                          or t.modelGreeks.delta is None):
                    self.ib.sleep(0.25)
                    g += 0.25
            if not _is_live(t):
                raise RuntimeError(
                    f"{contract.localSymbol or contract.symbol}: market data type "
                    f"{t.marketDataType} (delayed) — refusing to trade on it")
            return t
        finally:
            self.ib.cancelMktData(contract)

    def spot(self, symbol: str) -> float | None:
        t = self._snap(self._underlying(symbol))
        for v in (t.last, t.close, (t.bid + t.ask) / 2 if t.bid == t.bid else None):
            if v is not None and v == v and v > 0:
                return float(v)
        return None

    def close_on(self, symbol: str, day: str) -> float | None:
        """Underlying close on a specific past date, for a late settlement."""
        end = datetime.combine(date.fromisoformat(day), datetime.min.time())
        bars = self.ib.reqHistoricalData(
            self._underlying(symbol),
            endDateTime=end.replace(hour=23, minute=59).strftime("%Y%m%d %H:%M:%S")
            + " US/Eastern",
            durationStr="1 D", barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1, timeout=20)
        return float(bars[-1].close) if bars else None

    # --------------------------------------------------------------- chain

    def _chain(self, symbol: str):
        """Expirations and strikes for the SMART listing.

        Picks the chain whose tradingClass matches the symbol. The `2SPY`
        class also comes back from reqSecDefOptParams and carries only two
        far-dated expirations — taking the first SMART row blindly means never
        seeing a 0DTE contract at all.
        """
        if symbol not in self._chains:
            u = self._underlying(symbol)
            rows = self.ib.reqSecDefOptParams(u.symbol, "", u.secType, u.conId)
            match = [c for c in rows
                     if c.exchange == "SMART" and c.tradingClass == u.symbol]
            if not match:
                raise RuntimeError(f"{symbol}: no SMART/{u.symbol} option chain")
            c = max(match, key=lambda r: len(r.expirations))
            self._chains[symbol] = (sorted(c.expirations), sorted(c.strikes))
        return self._chains[symbol]

    def _quote(self, symbol: str, expiry_iso: str, right: str,
               strike: float) -> Quote | None:
        yyyymmdd = date.fromisoformat(expiry_iso).strftime("%Y%m%d")
        opt = Option(symbol, yyyymmdd, strike, right, "SMART", tradingClass=symbol)
        if not self.ib.qualifyContracts(opt):
            return None
        t = self._snap(opt, greeks=True)
        if not (t.bid == t.bid and t.ask == t.ask) or t.ask <= 0:
            return None
        g = t.modelGreeks
        delta = g.delta if g and g.delta is not None else \
            (0.5 if right == "C" else -0.5)
        return Quote(contract=occ_symbol(symbol, expiry_iso, right, strike),
                     strike=float(strike), bid=float(max(0.0, t.bid)),
                     ask=float(t.ask), delta=float(delta),
                     iv=None if not g else g.impliedVol)

    def chain_quote(self, symbol: str, expiry_iso: str, right: str,
                    spot: float) -> Quote | None:
        """Nearest-the-money contract with a two-sided quote. Walks outward a
        few strikes if the closest one is not quoting."""
        expirations, strikes = self._chain(symbol)
        if date.fromisoformat(expiry_iso).strftime("%Y%m%d") not in expirations:
            print(f"  ! {symbol}: no {expiry_iso} expiry in the chain")
            return None
        near = sorted((s for s in strikes if abs(s - spot) <= STRIKE_WINDOW),
                      key=lambda s: abs(s - spot))
        if not near:
            print(f"  ! {symbol}: no strike within ${STRIKE_WINDOW:g} of {spot:.2f}")
            return None
        for strike in near[:3]:
            q = self._quote(symbol, expiry_iso, right, strike)
            if q:
                return q
        return None

    def quote_position(self, pos) -> Quote | None:
        """Re-price a contract the book already holds."""
        return self._quote(pos.symbol, pos.expiry, pos.right, pos.strike)
