"""Run the bot through a trading session on a GitHub Actions runner.

GitHub caps a job at 6 hours and a session is 6.5, so this loops until the
16:01 ET cutoff OR its own time budget, whichever comes first. The workflow
fires hourly and queues one job behind the running one, so when this job
runs out of budget the next picks up within seconds.

Each tick is a separate `run_bot.py --once` process: a crash in one tick
cannot take down the loop. State lives in a checkout of the `state` branch
(ODTE_STATE_DIR) and is pushed whenever the book changes, plus every
PUSH_EVERY ticks for the log, plus once on exit.

SINCE 2026-10-17 THIS IS THE FALLBACK, NOT THE MAIN BOT. The Mac trades the
same book on live IBKR quotes and is strictly preferred. This session runs all
day regardless, but on each tick it asks state_repo whether the Mac has traded
recently and stands down if so. It only picks the book up when the Mac has
gone quiet for STALE_MIN minutes - a sleeping Mac, or a TWS that never got
started, which is what happened on all five sessions of 2026-10-12..16.

It keeps writing its own log while standing down, so the branch never goes
silent and kick.sh does not mistake a healthy standby for a zombie.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import state_repo as sr
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FIRST_TICK = dtime(9, 30)
LAST_TICK = dtime(16, 1)          # one tick past the close, to settle
MAX_EARLY = timedelta(minutes=170)  # wait for the open rather than hand off:
                                   # GitHub drops the morning slots, so an early
                                   # fire may be the only one we get all day
PUSH_EVERY = 5    # a heartbeat as much as a log push: the Mac watchdog calls
                  # a session dead when the state branch goes quiet, so pushing
                  # every 5 ticks lets it judge that after 8 minutes, not 14

BUDGET = timedelta(minutes=int(os.environ.get("BUDGET_MIN", "340")))
STATE = Path(os.environ["ODTE_STATE_DIR"]).resolve()
HERE = Path(__file__).parent


def now() -> datetime:
    return datetime.now(ET)


def book_fingerprint() -> tuple:
    # Contents, not mtime: Book.save() rewrites book.json on every tick.
    return tuple((p.read_bytes() if p.exists() else b"")
                 for p in (STATE / "book.json", STATE / "trades.csv"))


def main() -> int:
    start = now()
    open_at = datetime.combine(start.date(), FIRST_TICK, ET)
    stop_at = min(datetime.combine(start.date(), LAST_TICK, ET), start + BUDGET)

    if start.weekday() >= 5 or start >= stop_at:
        print(f"{start:%F %H:%M} ET: outside session, nothing to do")
        return 0
    if open_at - start > MAX_EARLY:
        print(f"{start:%F %H:%M} ET: too early, a later scheduled job covers the open")
        return 0

    # A separate file from the Mac's, so the two writers never collide on the
    # same lines; the book itself is still shared and guarded by the heartbeat.
    log = STATE / "logs" / f"{start:%F}.cloud.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"session job {start:%H:%M} ET -> stops {stop_at:%H:%M} ET", flush=True)

    ticks = 0
    while True:
        t = now()
        if t >= stop_at:
            break
        if t >= open_at:
            # Take whatever the Mac wrote before reading the book, every tick:
            # it may have traded since the last one.
            if not sr.sync():
                out = (f"{t:%H:%M:%S} ET  state branch will not sync — "
                       f"standing down rather than trading a stale book\n")
                ticks += 1
            elif (held := sr.stand_down()):
                out = f"{t:%H:%M:%S} ET  standby: {held}\n"
                ticks += 1
            else:
                before = book_fingerprint()
                # Explicitly Yahoo: there is no TWS on a runner, and "auto"
                # would take no tick at all.
                r = subprocess.run([sys.executable, str(HERE / "run_bot.py"),
                                    "--once", "--source", "yahoo"],
                                   capture_output=True, text=True, timeout=55)
                out = r.stdout + (f"[exit {r.returncode}] {r.stderr[-800:]}\n"
                                  if r.returncode else "")
                ticks += 1
                if book_fingerprint() != before:
                    print(out, end="", flush=True)
                    with log.open("a") as fh:
                        fh.write(out)
                    sr.persist(f"book {t:%F %H:%M} ET (cloud)")
                    time.sleep(max(1.0, 60 - now().second - now().microsecond / 1e6))
                    continue
            print(out, end="", flush=True)
            with log.open("a") as fh:
                fh.write(out)
            if ticks % PUSH_EVERY == 0:
                sr.persist(f"log {t:%F %H:%M} ET (cloud)")
        # sleep to the top of the next minute
        time.sleep(max(1.0, 60 - now().second - now().microsecond / 1e6))

    sr.persist(f"end {now():%F %H:%M} ET ({ticks} ticks, cloud)")
    print(f"job done: {ticks} ticks", flush=True)
    # Out of budget with the session still open: start the successor now
    # rather than hoping a cron slot fires. GitHub lets a GITHUB_TOKEN
    # dispatch a new run (the one exception to its no-recursion rule).
    session_end = datetime.combine(start.date(), LAST_TICK, ET)
    if now() < session_end - timedelta(minutes=2):
        r = subprocess.run(["gh", "workflow", "run", "session.yml"],
                           capture_output=True, text=True)
        print(f"handoff: dispatched successor (exit {r.returncode}) {r.stderr.strip()[:200]}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
