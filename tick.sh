#!/bin/bash
# One bot tick, guarded. launchd fires this every 60s all day; it exits fast
# outside regular trading hours so the log stays readable.
cd "$(dirname "$0")" || exit 1

DOW=$(TZ=America/New_York date +%u)      # 1=Mon .. 7=Sun
HHMM=$(TZ=America/New_York date +%H%M)
[ "$DOW" -gt 5 ] && exit 0
[ "$HHMM" \< "0930" ] && exit 0
[ "$HHMM" \> "1600" ] && exit 0

# mkdir is atomic: if a slow yfinance call makes a tick outrun its 60s slot,
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

exec ./venv/bin/python run_bot.py --once >> logs/bot.log 2>&1
