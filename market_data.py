"""The shape of a priced option contract, shared by every data source.

Deliberately dependency-free: the cloud fallback installs neither ib_async nor
anything else beyond pandas, so this module must import cleanly with no broker
libraries present.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Quote:
    """One option contract, priced.

    `tbl` and `chain_ref` are Yahoo-only. They carry the raw chain table and
    the put-call-parity estimate of what the underlying was when that chain
    was quoted, which is what run_bot needs to undo Yahoo's lag. A live source
    leaves both None because there is no lag to undo.
    """
    contract: str                 # OCC symbol, e.g. SPY261009C00777000
    strike: float
    bid: float
    ask: float
    delta: float
    iv: float | None = None
    tbl: object = None
    chain_ref: float | None = None


def occ_symbol(root: str, expiry_iso: str, right: str, strike: float) -> str:
    """The canonical contract id used in trades.csv. Identical to what Yahoo
    returns, so the log stays comparable across a change of data source.
    IBKR's own localSymbol is the same string with the root space-padded."""
    d = date.fromisoformat(expiry_iso)
    return f"{root}{d:%y%m%d}{right}{int(round(strike * 1000)):08d}"
