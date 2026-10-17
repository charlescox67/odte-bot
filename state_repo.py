"""The `state` branch, shared by two writers that must never both trade.

Since 2026-10-17 the book has two possible writers:

  * the **Mac**, which reads live IBKR quotes but only exists when the machine
    is awake and TWS is logged in;
  * the **cloud** (GitHub Actions), which reads Yahoo on the 20-minute lagged
    clock and is always there.

The Mac is strictly preferred, because its data is live. The cloud only trades
when the Mac has not. That is decided by a heartbeat: whoever completes a tick
stamps `heartbeat.json`, and the cloud stands down while the Mac's stamp is
fresher than STALE_MIN.

The heartbeat means "I TRADED", not "I am alive". Between 2026-10-12 and
2026-10-16 the Mac woke, ticked 913 times and reached a TWS that was not
running: a liveness heartbeat would have told the cloud to stand down for five
straight sessions and nobody would have traded at all. So run_bot stamps it
only after a tick that had a working data source.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE = Path(os.environ.get("ODTE_STATE_DIR") or Path(__file__).parent)
BEAT = STATE / "heartbeat.json"
# How long the other writer's silence has to last before the cloud takes over.
#
# MUST exceed the push cadence with margin. The heartbeat only reaches the
# branch when someone pushes, and the Mac pushes every 5 minutes unless the
# book moved, so a perfectly healthy Mac can show a 5-minute-old stamp. At
# STALE_MIN = 6 that is a coin flip and the two would trade over each other.
# 12 gives two full push cycles of slack; the cost is that a Mac which dies
# mid-session leaves the book unattended for up to 12 minutes.
STALE_MIN = 12
WRITER = os.environ.get("ODTE_WRITER", "mac")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(STATE), *args],
                          capture_output=True, text=True, timeout=60)


# ------------------------------------------------------------------ heartbeat

def read_beat() -> dict | None:
    try:
        return json.loads(BEAT.read_text())
    except Exception:
        return None


def write_beat(source: str) -> None:
    """Stamp the branch: THIS writer just completed a tick with live data."""
    BEAT.write_text(json.dumps({
        "writer": WRITER,
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=1) + "\n")


def beat_age(beat: dict | None) -> timedelta | None:
    if not beat or "ts" not in beat:
        return None
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(beat["ts"])
    except Exception:
        return None


def stand_down() -> str | None:
    """Is the OTHER writer currently on the book? Returns why, or None to trade.

    Only the cloud ever stands down. The Mac is the preferred writer and takes
    the book whenever it can actually trade, which by then means TWS answered.
    """
    if WRITER == "mac":
        return None
    beat = read_beat()
    if not beat or beat.get("writer") == WRITER:
        return None
    age = beat_age(beat)
    if age is None or age > timedelta(minutes=STALE_MIN):
        return None
    return (f"{beat['writer']} traded {int(age.total_seconds())}s ago "
            f"on {beat.get('source', '?')}")


# ----------------------------------------------------------------- git plumbing

def sync() -> bool:
    """Take whatever the other writer left, before trusting the local book.

    Returns False if the two have genuinely diverged. The caller must NOT
    trade on a False: trading on a stale book would silently overwrite the
    other writer's trades on the next push.
    """
    if git("fetch", "-q", "origin", "state").returncode != 0:
        return False
    ahead = git("rev-list", "--count", "FETCH_HEAD..HEAD").stdout.strip()
    if ahead in ("", "0"):
        # Nothing of ours is unpushed: match origin exactly.
        return git("reset", "--quiet", "--hard", "FETCH_HEAD").returncode == 0
    return git("merge", "--quiet", "--ff-only", "FETCH_HEAD").returncode == 0


def persist(msg: str) -> bool:
    """Commit and push, rebasing onto the other writer if it got there first."""
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return True                       # nothing changed; nothing to say
    if git("commit", "-q", "-m", msg).returncode != 0:
        return False
    for attempt in range(3):
        if git("push", "-q", "origin", "HEAD:state").returncode == 0:
            return True
        # Rejected: the other writer pushed between our sync and our push.
        # Replay our commit on top of theirs rather than clobbering it.
        git("fetch", "-q", "origin", "state")
        if git("rebase", "--quiet", "FETCH_HEAD").returncode != 0:
            git("rebase", "--abort")
            print(f"  ! state: cannot rebase onto origin, push abandoned",
                  flush=True)
            return False
        time.sleep(2 * (attempt + 1))
    print("  ! state: push failed three times", flush=True)
    return False
