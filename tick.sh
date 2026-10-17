#!/bin/bash
# One bot tick, guarded. launchd fires this every 60s all day; it exits fast
# outside regular trading hours so the log stays readable.
#
# Since 2026-10-09 the Mac is the ONLY writer: the bot reads live IBKR quotes
# from TWS, which only runs here, so the GitHub Actions schedule is off. State
# still lives on the `state` branch (so the book is readable from anywhere and
# survives this machine), checked out as a worktree in ./state and pushed
# whenever it changes.
cd "$(dirname "$0")" || exit 1

# FORCE_WINDOW and DRY_RUN exist only so this can be exercised out of hours;
# same hooks as kick.sh. DRY_RUN does everything but the push.
if [ "${FORCE_WINDOW:-0}" != "1" ]; then
  DOW=$(TZ=America/New_York date +%u)      # 1=Mon .. 7=Sun
  HHMM=$(TZ=America/New_York date +%H%M)
  [ "$DOW" -gt 5 ] && exit 0
  [ "$HHMM" \< "0930" ] && exit 0
  [ "$HHMM" \> "1600" ] && exit 0
fi

# mkdir is atomic: if a slow quote request makes a tick outrun its 60s slot,
# the next one skips rather than double-trading the same signal.
LOCK=".tick.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +5 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

STATE="$PWD/state"
export ODTE_STATE_DIR="$STATE"
TODAY=$(TZ=America/New_York date +%F)

if [ ! -e "$STATE/.git" ]; then
  git fetch -q origin state \
    && git worktree add -q "$STATE" origin/state \
    && git -C "$STATE" checkout -q -B state \
    || { echo "$(date): could not create the state worktree" >> logs/bot.log; exit 1; }
fi

# Take whatever the cloud session left behind BEFORE reading the book - it may
# have traded while this Mac was asleep or TWS was down. If the two have
# genuinely diverged, do not trade: a stale book would overwrite the cloud's
# trades on the next push.
if ! ./venv/bin/python -c 'import state_repo as sr; raise SystemExit(0 if sr.sync() else 1)'; then
  echo "$(TZ=America/New_York date '+%F %T') ET  state branch will not sync — no tick" \
    | tee -a logs/bot.log >> "$STATE/logs/$TODAY.log"
  exit 1
fi

mkdir -p "$STATE/logs"
./venv/bin/python run_bot.py --once 2>&1 \
  | tee -a logs/bot.log >> "$STATE/logs/$TODAY.log"

# Push when the book moved, and otherwise every 5th minute. The heartbeat
# rides along: state_repo.STALE_MIN is set well above this cadence precisely
# because a healthy Mac's stamp can be five minutes old on the branch.
if ! git -C "$STATE" diff --quiet -- book.json trades.csv \
   || [ "$(( 10#$(TZ=America/New_York date +%M) % 5 ))" -eq 0 ]; then
  [ "${DRY_RUN:-0}" = "1" ] && { echo "DRY RUN: would commit + push"; exit 0; }
  ./venv/bin/python -c "import state_repo as sr; sr.persist('book $(TZ=America/New_York date '+%F %H:%M') ET (mac)')" \
    >> logs/bot.log 2>&1
fi
