#!/bin/bash
# Hold the system awake through the trading session.
#
# This is what makes the scheduled wake stick. launchd fires it every 60s; the
# first firing after a wake exec's caffeinate, which holds the machine up to
# 16:05 ET. If this script exits (outside its window) nothing asserts and the
# Mac returns to sleep within a minute or two.
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
# 09:10, not 09:25. The scheduled wake (pmset repeat wakepoweron) has to land
# INSIDE this window or it achieves nothing: on 2026-10-12 the Mac woke on
# schedule at 07:20 ET, found this script two hours outside its window, got no
# caffeinate assertion, and went back to sleep 68 seconds later. It stayed
# asleep through the open and the entire entry window. Keep the wake time at
# or after this one, with a few minutes of margin for launchd to fire.
[ "$HHMM" \< "0910" ] && exit 0
[ "$HHMM" \> "1605" ] && exit 0

TODAY=$(TZ=America/New_York date +%Y-%m-%d)
END=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M:%S" "$TODAY 16:05:00" +%s 2>/dev/null) || exit 0
SECS=$(( END - $(date +%s) ))
[ "$SECS" -le 0 ] && exit 0

# -i prevents idle system sleep and DOES work on battery; -s only asserts on AC.
# No -d/-u: the display is free to sleep.
echo "$(TZ=America/New_York date '+%F %T') ET  holding awake ${SECS}s to 16:05 ET" >> logs/caffeine.log
exec caffeinate -ims -t "$SECS"
