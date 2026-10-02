#!/bin/bash
# Keeps the cloud session alive from the Mac. Two jobs:
#
#  1. STARTER - GitHub's scheduler drops runs (it has never once fired a
#     morning slot), so dispatch a session when none is running.
#  2. WATCHDOG - on 2026-10-02 a runner died at 14:00 ET and GitHub left the
#     job marked in_progress until 14:45. The concurrency group blocked the
#     queued replacement for all 45 minutes, so an open position went
#     unmanaged. Dense cron cannot fix that: the zombie holds the slot. So if
#     a run looks active but the bot has not written to the `state` branch for
#     STALE_MIN minutes, cancel it and dispatch a replacement.
#
# Env overrides exist for testing: STALE_MIN, DRY_RUN=1, FORCE_WINDOW=1.
# launchd gives a minimal PATH, so set a known one. KICK_BIN prepends a
# directory and exists only so tests can stub out `gh`.
export PATH="${KICK_BIN:+$KICK_BIN:}/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")" || exit 1
STALE_MIN=${STALE_MIN:-8}       # the bot pushes at least every 5 ticks
DRY_RUN=${DRY_RUN:-0}
log() { echo "$(TZ=America/New_York date '+%F %T') ET  $1" >> logs/kick.log; }
act() { [ "$DRY_RUN" = "1" ] && { log "DRY RUN would: $*"; return 0; }; "$@"; }

DOW=$(TZ=America/New_York date +%u)
HHMM=$(TZ=America/New_York date +%H%M)
if [ "${FORCE_WINDOW:-0}" != "1" ]; then
  [ "$DOW" -gt 5 ] && exit 0
  if [ "$HHMM" \< "0900" ] || [ "$HHMM" \> "1530" ]; then
    # Runs every few minutes, so this would flood the log: heartbeat to a file.
    echo "$(TZ=America/New_York date '+%F %T') ET outside 09:00-15:30" > logs/kick.last
    exit 0
  fi
fi

# On a scheduled wake the network is often not up yet, so retry rather than
# waiting for the next run - by then the open has passed.
for attempt in 1 2 3 4; do
  RUNS=$(gh run list --workflow session.yml --limit 10 \
         --json status,databaseId,createdAt 2>>logs/kick.err)
  [ -n "$RUNS" ] && break
  log "GitHub unreachable (attempt $attempt) - waiting for the network"
  sleep 20
done
if [ -z "$RUNS" ]; then
  log "could not reach GitHub (gh failed) - see kick.err"; exit 0
fi

ACTIVE=$(printf '%s' "$RUNS" | jq '[.[] | select(.status=="in_progress" or .status=="queued" or .status=="pending")] | length')
echo "$(TZ=America/New_York date '+%F %T') ET active=$ACTIVE" > logs/kick.last

if [ "$ACTIVE" = "0" ]; then
  act gh workflow run session.yml 2>>logs/kick.err && log "no session running -> dispatched"
  exit 0
fi

# Something is active. Is it alive? The bot pushes the log every 10 ticks and
# the book whenever it changes, so silence longer than STALE_MIN means dead.
git fetch -q origin state 2>>logs/kick.err
LAST=$(git log -1 --format=%ct origin/state 2>>logs/kick.err)
NOW=$(date +%s)
RUN_ID=$(printf '%s' "$RUNS" | jq -r 'map(select(.status=="in_progress")) | sort_by(.createdAt) | last | .databaseId // empty')
RUN_AGE=$(printf '%s' "$RUNS" | jq -r --arg now "$NOW" 'map(select(.status=="in_progress")) | sort_by(.createdAt) | last | if . == null then 0 else (($now|tonumber) - (.createdAt|fromdate)) / 60 | floor end')
[ -z "$LAST" ] && { log "could not read the state branch - left $ACTIVE run(s) alone"; exit 0; }
STALE=$(( (NOW - LAST) / 60 ))

# A freshly started run has not pushed yet, so only judge runs older than the
# same threshold - otherwise the watchdog kills healthy new sessions.
if [ -n "$RUN_ID" ] && [ "$STALE" -ge "$STALE_MIN" ] && [ "${RUN_AGE:-0}" -ge "$STALE_MIN" ]; then
  log "ZOMBIE: run $RUN_ID is ${RUN_AGE}m old but state is ${STALE}m stale -> cancel + redispatch"
  act gh run cancel "$RUN_ID" 2>>logs/kick.err
  sleep 8
  act gh workflow run session.yml 2>>logs/kick.err && log "replacement dispatched"
else
  echo "$(TZ=America/New_York date '+%F %T') ET active=$ACTIVE state ${STALE}m old" > logs/kick.last
fi
