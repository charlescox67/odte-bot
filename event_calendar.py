"""Scheduled macro events that change how the bot trades a given day.

Sources (fetched 2026-09-21):
  BLS release calendar  https://www.bls.gov/schedule/2026/home.htm
  FOMC calendar         https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

CPI, PPI and the jobs report print at 08:30 ET, before the open: those days
trade at half risk. FOMC statements land at 14:00 ET: no entries after 13:00
and anything open is flattened at 13:55 (both on the lagged market clock the
bot fills at, so the exits price before the statement).

EXTEND THIS before COVERED_THROUGH passes — the bot warns loudly once it has.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

CPI = {date(2026, 9, 11), date(2026, 10, 14), date(2026, 11, 10), date(2026, 12, 10)}
PPI = {date(2026, 9, 10), date(2026, 10, 15), date(2026, 11, 13), date(2026, 12, 15)}
NFP = {date(2026, 9, 4), date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4)}
# Decision day = the second day of each two-day meeting.
FOMC = {date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
        date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9)}
COVERED_THROUGH = date(2026, 12, 31)

DATA_RISK_MULT = 0.5
FOMC_ENTRY_CUTOFF = time(13, 0)
FOMC_FLATTEN = time(13, 55)


@dataclass(frozen=True)
class DayProfile:
    label: str                  # "" on an ordinary day
    risk_mult: float = 1.0
    entry_cutoff: time | None = None
    flatten_at: time | None = None
    covered: bool = True


def day_profile(d: date) -> DayProfile:
    covered = d <= COVERED_THROUGH
    if d in FOMC:
        return DayProfile("FOMC", 1.0, FOMC_ENTRY_CUTOFF, FOMC_FLATTEN, covered)
    tags = [n for n, s in (("CPI", CPI), ("PPI", PPI), ("NFP", NFP)) if d in s]
    if tags:
        return DayProfile("+".join(tags), DATA_RISK_MULT, covered=covered)
    return DayProfile("", covered=covered)
