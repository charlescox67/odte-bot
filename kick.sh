#!/bin/bash
# Backup starter for the cloud session. GitHub's scheduler drops runs, so the
# Mac nudges it: every 30 min while awake during market hours, dispatch a run
# unless one is already running or queued. Harmless if cron already worked.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")" || exit 1
DOW=$(TZ=America/New_York date +%u)
HHMM=$(TZ=America/New_York date +%H%M)
log() { echo "$(TZ=America/New_York date '+%F %T') ET  $1" >> logs/kick.log; }
[ "$DOW" -gt 5 ] && exit 0
if [ "$HHMM" \< "0900" ] || [ "$HHMM" \> "1530" ]; then
  log "asleep or outside 09:00-15:30 ET ($HHMM) - nothing to do"; exit 0
fi
# On a scheduled wake the network is often not up yet, so retry rather than
# waiting 30 minutes for the next run - by then the open has passed.
for attempt in 1 2 3 4; do
  ACTIVE=$(gh run list --workflow session.yml --limit 10 --json status \
           --jq '[.[] | select(.status=="in_progress" or .status=="queued" or .status=="pending")] | length' 2>>logs/kick.err)
  [ -n "$ACTIVE" ] && break
  log "GitHub unreachable (attempt $attempt) - waiting for the network"
  sleep 20
done
if [ "$ACTIVE" = "0" ]; then
  gh workflow run session.yml 2>>logs/kick.err && log "no session running -> dispatched"
elif [ -z "$ACTIVE" ]; then
  log "could not reach GitHub (gh failed) - see kick.err"
else
  log "$ACTIVE session run(s) already active - left alone"
fi
