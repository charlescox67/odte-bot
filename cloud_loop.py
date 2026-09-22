"""Run the bot through a trading session on a GitHub Actions runner.

GitHub caps a job at 6 hours and a session is 6.5, so this loops until the
16:01 ET cutoff OR its own time budget, whichever comes first. The workflow
fires hourly and queues one job behind the running one, so when this job
runs out of budget the next picks up within seconds.

Each tick is a separate `run_bot.py --once` process: a crash in one tick
cannot take down the loop. State lives in a checkout of the `state` branch
(ODTE_STATE_DIR) and is pushed whenever the book changes, plus every
PUSH_EVERY ticks for the log, plus once on exit.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FIRST_TICK = dtime(9, 30)
LAST_TICK = dtime(16, 1)          # one tick past the close, to settle
MAX_EARLY = timedelta(minutes=45)  # started earlier than this? a later job has it
PUSH_EVERY = 10

BUDGET = timedelta(minutes=int(os.environ.get("BUDGET_MIN", "340")))
STATE = Path(os.environ["ODTE_STATE_DIR"]).resolve()
HERE = Path(__file__).parent


def now() -> datetime:
    return datetime.now(ET)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(STATE), *args],
                          capture_output=True, text=True)


def persist(msg: str) -> None:
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("commit", "-q", "-m", msg)
    for i in range(3):
        r = git("push", "-q", "origin", "HEAD:state")
        if r.returncode == 0:
            return
        print(f"  ! state push failed ({i+1}/3): {r.stderr.strip()[:200]}", flush=True)
        time.sleep(5 * (i + 1))


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

    log = STATE / "logs" / f"{start:%F}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"session job {start:%H:%M} ET -> stops {stop_at:%H:%M} ET", flush=True)

    ticks = 0
    while True:
        t = now()
        if t >= stop_at:
            break
        if t >= open_at:
            before = book_fingerprint()
            r = subprocess.run([sys.executable, str(HERE / "run_bot.py"), "--once"],
                               capture_output=True, text=True, timeout=55)
            out = r.stdout + (f"[exit {r.returncode}] {r.stderr[-800:]}\n"
                              if r.returncode else "")
            print(out, end="", flush=True)
            with log.open("a") as fh:
                fh.write(out)
            ticks += 1
            if book_fingerprint() != before:
                persist(f"book {t:%F %H:%M} ET")
            elif ticks % PUSH_EVERY == 0:
                persist(f"log {t:%F %H:%M} ET")
        # sleep to the top of the next minute
        time.sleep(max(1.0, 60 - now().second - now().microsecond / 1e6))

    persist(f"end {now():%F %H:%M} ET ({ticks} ticks)")
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
