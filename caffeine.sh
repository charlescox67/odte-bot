#!/bin/bash
# Hold the Mac awake through the trading session so stops keep getting checked.
# launchd fires this every 60s; it is idempotent — it starts caffeinate once and
# then does nothing until the session ends.
cd "$(dirname "$0")" || exit 1
PIDFILE=".caffeinate.pid"

DOW=$(TZ=America/New_York date +%u)
HHMM=$(TZ=America/New_York date +%H%M)

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

if [ "$DOW" -le 5 ] && [ "$HHMM" \> "0924" ] && [ "$HHMM" \< "1606" ]; then
  running && exit 0
  # Self-limiting: caffeinate is given the seconds remaining to 16:05 ET, so it
  # releases on its own even if this agent is unloaded mid-session.
  TODAY=$(TZ=America/New_York date +%Y-%m-%d)
  END=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M:%S" "$TODAY 16:05:00" +%s 2>/dev/null)
  SECS=$(( END - $(date +%s) ))
  [ -z "$END" ] || [ "$SECS" -le 0 ] && exit 0
  # No -d: the display may sleep, only the SYSTEM must stay awake.
  caffeinate -imsu -t "$SECS" &
  echo $! > "$PIDFILE"
  echo "$(date '+%F %T') caffeinate up for ${SECS}s (to 16:05 ET)" >> logs/caffeine.log
else
  if running; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
    echo "$(date '+%F %T') caffeinate released" >> logs/caffeine.log
  fi
  rm -f "$PIDFILE"
fi
