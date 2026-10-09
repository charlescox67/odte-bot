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

DOW=$(TZ=America/New_York date +%u)      # 1=Mon .. 7=Sun
HHMM=$(TZ=America/New_York date +%H%M)
[ "$DOW" -gt 5 ] && exit 0
[ "$HHMM" \< "0930" ] && exit 0
[ "$HHMM" \> "1600" ] && exit 0

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

# First tick of the day: take whatever was left on the branch before trusting
# the local copy. The marker lives outside the worktree so `git add -A` in
# here never sees it.
if [ ! -f "logs/.synced-$TODAY" ]; then
  if git -C "$STATE" fetch -q origin state \
     && git -C "$STATE" merge -q --ff-only FETCH_HEAD 2>/dev/null; then
    rm -f logs/.synced-* ; touch "logs/.synced-$TODAY"
  else
    echo "$(date): state branch would not fast-forward — LOCAL BOOK MAY BE STALE" \
      >> logs/bot.log
  fi
fi

mkdir -p "$STATE/logs"
./venv/bin/python run_bot.py --once 2>&1 \
  | tee -a logs/bot.log >> "$STATE/logs/$TODAY.log"

# Push when the book moved, and otherwise every 5th minute so the log on the
# branch (and anyone reading it) stays close to live. Same cadence as the old
# cloud loop's PUSH_EVERY.
git -C "$STATE" add -A
if git -C "$STATE" diff --cached --quiet; then
  exit 0
fi
if ! git -C "$STATE" diff --cached --quiet -- book.json trades.csv \
   || [ "$(( 10#$(TZ=America/New_York date +%M) % 5 ))" -eq 0 ]; then
  git -C "$STATE" commit -q -m "book $(TZ=America/New_York date '+%F %H:%M') ET"
  for i in 1 2 3; do
    git -C "$STATE" push -q origin HEAD:state && break
    echo "$(date): state push failed ($i/3)" >> logs/bot.log
    sleep $((5 * i))
  done
else
  git -C "$STATE" reset -q           # leave it staged-free until the next push
fi
