#!/bin/bash
# Backup starter for the cloud session. GitHub's scheduler drops runs, so the
# Mac nudges it: every 30 min while awake during market hours, dispatch a run
# unless one is already running or queued. Harmless if cron already worked.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")" || exit 1
DOW=$(TZ=America/New_York date +%u)
HHMM=$(TZ=America/New_York date +%H%M)
[ "$DOW" -gt 5 ] && exit 0
[ "$HHMM" \< "0900" ] && exit 0
[ "$HHMM" \> "1530" ] && exit 0
ACTIVE=$(gh run list --workflow session.yml --limit 10 --json status \
         --jq '[.[] | select(.status=="in_progress" or .status=="queued" or .status=="pending")] | length' 2>>logs/kick.err)
if [ "$ACTIVE" = "0" ]; then
  gh workflow run session.yml 2>>logs/kick.err \
    && echo "$(TZ=America/New_York date '+%F %T') ET  no session running -> dispatched" >> logs/kick.log
fi
