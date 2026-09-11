#!/bin/bash
# Hold the system awake through the trading session.
#
# This script deliberately EXECs caffeinate rather than backgrounding it.
# launchd reaps a job's process group when the job's main process exits, so
# `caffeinate ... &` dies seconds after launch. By exec'ing, caffeinate BECOMES
# the job: launchd keeps it alive, sees the job still running, and skips its
# 60s re-fire until it exits. No pidfile, nothing to go stale.
# (Note this is the opposite of tick.sh, where exec would kill its EXIT trap.)
cd "$(dirname "$0")" || exit 1

DOW=$(TZ=America/New_York date +%u)
HHMM=$(TZ=America/New_York date +%H%M)
[ "$DOW" -gt 5 ] && exit 0
[ "$HHMM" \< "0925" ] && exit 0
[ "$HHMM" \> "1605" ] && exit 0

TODAY=$(TZ=America/New_York date +%Y-%m-%d)
END=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M:%S" "$TODAY 16:05:00" +%s 2>/dev/null) || exit 0
SECS=$(( END - $(date +%s) ))
[ "$SECS" -le 0 ] && exit 0

# -i prevents idle system sleep and DOES work on battery; -s only asserts on AC.
# No -d/-u: the display is free to sleep.
echo "$(TZ=America/New_York date '+%F %T') ET  holding awake ${SECS}s to 16:05 ET" >> logs/caffeine.log
exec caffeinate -ims -t "$SECS"
