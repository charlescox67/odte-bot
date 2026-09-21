"""A self-contained simulated broker. No IBKR, no money, no order routing.

Positions live in a JSON file so the book survives restarts and spans days.
Every closed trade is appended to a CSV — that log is the whole point: without
it you cannot estimate a win rate, and without a win rate position sizing is
guesswork.

FILL MODEL: buy at the ASK, sell at the BID. Always. Real paper platforms fill
at the midpoint or better, which quietly hands you the spread twice per trade.
On a 0DTE ATM contract the spread is ~2% of premium, so that flattery compounds
into a materially wrong answer over a few hundred trades.
"""
from __future__ import annotations

import csv
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
from pathlib import Path

# On the cloud runner, state lives in a checkout of the `state` branch.
STATE_DIR = Path(os.environ.get("ODTE_STATE_DIR") or Path(__file__).parent)
BOOK = STATE_DIR / "book.json"
TRADES = STATE_DIR / "trades.csv"
MULTIPLIER = 100  # US equity and index options
TRADE_COLUMNS = ["id", "symbol", "contract", "right", "strike", "expiry", "qty",
                 "entry_time", "entry_price", "underlying_at_entry", "entry_reason",
                 "exit_time", "exit_price", "exit_reason", "cost", "proceeds",
                 "pnl", "pnl_pct",
                 "signal_symbol", "setup", "stop_at_entry", "stop_final",
                 "spread_at_entry", "event_day"]


@dataclass
class Position:
    id: str
    symbol: str            # underlying, e.g. SPY
    contract: str          # OCC symbol
    right: str             # C or P
    strike: float
    expiry: str            # YYYY-MM-DD
    qty: int
    entry_price: float     # per share, what we paid (the ask)
    entry_time: str
    entry_reason: str
    underlying_at_entry: float
    status: str = "open"
    mark: float = 0.0
    exit_price: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    # Swing-strategy context. Defaults keep older book.json files loadable.
    signal_symbol: str | None = None     # chart the stop is measured on (SPY for SPX)
    setup: str = ""                      # pivot id, e.g. "L1005": one trade per pivot
    stop_at_entry: float | None = None   # underlying level
    stop_underlying: float | None = None # current, after trailing
    spread_at_entry: float | None = None
    event_day: str = ""

    @property
    def cost(self) -> float:
        return self.entry_price * self.qty * MULTIPLIER

    def pnl(self, price: float | None = None) -> float:
        px = self.exit_price if self.exit_price is not None else (
            price if price is not None else self.mark)
        return (px - self.entry_price) * self.qty * MULTIPLIER

    def pnl_pct(self, price: float | None = None) -> float:
        if not self.entry_price:
            return 0.0
        return self.pnl(price) / self.cost


@dataclass
class Book:
    cash: float = 100_000.0
    positions: list[Position] = field(default_factory=list)

    # ---------- persistence ----------
    @classmethod
    def load(cls, path: Path = BOOK) -> "Book":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(cash=raw["cash"],
                   positions=[Position(**p) for p in raw["positions"]])

    def save(self, path: Path = BOOK) -> None:
        # Write-then-rename: a crash mid-write must not corrupt the book.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"cash": self.cash, "positions": [asdict(p) for p in self.positions]},
            indent=2))
        os.replace(tmp, path)

    # ---------- book ops ----------
    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    def open(self, *, symbol: str, contract: str, right: str, strike: float,
             expiry: str, qty: int, ask: float, underlying: float,
             reason: str, ts: datetime | None = None, **context) -> Position:
        if ask <= 0:
            raise ValueError(f"refusing to open {contract} at ask={ask}")
        ts = ts or datetime.now(timezone.utc)
        pos = Position(
            id=uuid.uuid4().hex[:8], symbol=symbol, contract=contract,
            right=right, strike=strike, expiry=expiry, qty=qty,
            entry_price=ask, entry_time=ts.isoformat(), entry_reason=reason,
            underlying_at_entry=underlying, mark=ask, **context)
        cost = pos.cost
        if cost > self.cash:
            raise ValueError(f"insufficient cash: need {cost:.2f} have {self.cash:.2f}")
        self.cash -= cost
        self.positions.append(pos)
        return pos

    def close(self, pos: Position, *, bid: float, reason: str,
              ts: datetime | None = None) -> Position:
        ts = ts or datetime.now(timezone.utc)
        pos.exit_price = max(0.0, bid)
        pos.exit_time = ts.isoformat()
        pos.exit_reason = reason
        pos.status = "closed"
        pos.mark = pos.exit_price
        self.cash += pos.exit_price * pos.qty * MULTIPLIER
        self._log(pos)
        return pos

    def settle_expired(self, price_for, now: datetime | None = None) -> list[Position]:
        """A 0DTE contract has no next day. At expiry it becomes intrinsic value.

        `price_for(pos)` must return the underlying price ON THAT POSITION'S
        EXPIRY DATE, not today's. If the bot is asleep at 16:00 and settles a
        day late, using the current spot books a P&L from the wrong session.
        """
        # A contract expires at the 16:00 ET close, not at midnight. Comparing
        # dates alone settled every 0DTE trade the instant it was opened.
        now = (now or datetime.now(ET)).astimezone(ET)
        done = []
        for pos in self.open_positions:
            expiry = date.fromisoformat(pos.expiry)
            if expiry > now.date():
                continue
            if expiry == now.date() and now.time() < MARKET_CLOSE:
                continue
            spot = price_for(pos)
            if spot is None:
                continue
            intrinsic = (max(0.0, spot - pos.strike) if pos.right == "C"
                         else max(0.0, pos.strike - spot))
            done.append(self.close(pos, bid=intrinsic, reason="expired"))
        return done

    def _log(self, pos: Position) -> None:
        # Appending under a different header would misalign every column, so
        # an older layout is upgraded in place first (see _upgrade_log).
        if TRADES.exists():
            _upgrade_log()
        new = not TRADES.exists()
        with TRADES.open("a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(TRADE_COLUMNS)
            w.writerow([pos.id, pos.symbol, pos.contract, pos.right, pos.strike,
                        pos.expiry, pos.qty, pos.entry_time, pos.entry_price,
                        pos.underlying_at_entry, pos.entry_reason, pos.exit_time,
                        pos.exit_price, pos.exit_reason,
                        f"{pos.cost:.2f}", f"{pos.exit_price * pos.qty * MULTIPLIER:.2f}",
                        f"{pos.pnl():.2f}", f"{pos.pnl_pct():.4f}",
                        pos.signal_symbol or "", pos.setup, pos.stop_at_entry,
                        pos.stop_underlying, pos.spread_at_entry, pos.event_day])

    def equity(self) -> float:
        return self.cash + sum(p.mark * p.qty * MULTIPLIER for p in self.open_positions)


def _upgrade_log() -> None:
    """Bring trades.csv to TRADE_COLUMNS without losing history.

    Rows from an older layout are rewritten by column NAME, and the dollar
    columns are derived from price x qty x 100. A file with columns this code
    doesn't know is moved aside instead, since it can't be mapped safely."""
    with TRADES.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = list(rows[0].keys()) if rows else None
    if header is None or header == TRADE_COLUMNS:
        return
    if not set(header) <= set(TRADE_COLUMNS):
        TRADES.rename(TRADES.with_name(f"trades.old-{uuid.uuid4().hex[:6]}.csv"))
        return
    for r in rows:
        mult = float(r["qty"]) * MULTIPLIER
        r.setdefault("cost", f"{float(r['entry_price']) * mult:.2f}")
        if "proceeds" not in r:
            r["proceeds"] = (f"{float(r['exit_price']) * mult:.2f}"
                             if r.get("exit_price") else "")
    tmp = TRADES.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLUMNS, restval="")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, TRADES)


def occ(symbol: str, expiry: str, right: str, strike: float) -> str:
    y, m, d = expiry.split("-")
    return f"{symbol}{y[2:]}{m}{d}{right.upper()}{int(round(strike*1000)):08d}"
