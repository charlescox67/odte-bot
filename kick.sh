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
# EVERY network call is wrapped in a timeout. On 2026-10-05 `gh workflow run`
# hung for 74 minutes: the script never logged or exited, and launchd will not
# start a second copy of a job that is still running, so the watchdog meant to
# catch a hung session was itself hung all morning. Nothing here may block.
#
# Env overrides exist for testing: STALE_MIN, DRY_RUN=1, FORCE_WINDOW=1.
# launchd gives a minimal PATH, so set a known one. KICK_BIN prepends a
# directory and exists only so tests can stub out `gh`.
export PATH="${KICK_BIN:+$KICK_BIN:}/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")" || exit 1
STALE_MIN=${STALE_MIN:-8}       # the bot pushes at least every 5 ticks
DRY_RUN=${DRY_RUN:-0}
NET_TIMEOUT=${NET_TIMEOUT:-45}  # hard ceiling on any single network call
FIRST_PUSH_MIN=7                # a live session pushes within ~6 min of its first tick
log() { echo "$(TZ=America/New_York date '+%F %T') ET  $1" >> logs/kick.log; }
# macOS has no coreutils `timeout`, so run the command in the background and
# kill it if it overruns. Returns 124 on timeout, like GNU timeout does.
run() {
  "$@" & local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    [ "$waited" -ge "$NET_TIMEOUT" ] && { kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; return 124; }
    sleep 1; waited=$((waited + 1))
  done
  wait "$pid"
}
act() { [ "$DRY_RUN" = "1" ] && { log "DRY RUN would: $*"; return 0; }; run "$@"; }

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
  RUNS=$(run gh run list --workflow session.yml --limit 10 \
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
  if act gh workflow run session.yml 2>>logs/kick.err; then
    log "no session running -> dispatched"
  else
    log "dispatch failed or timed out (exit $?) - will retry next run"
  fi
  exit 0
fi

# Something is active. Is it alive? The bot pushes the log every 10 ticks and
# the book whenever it changes, so silence longer than STALE_MIN means dead.
run git fetch -q origin state 2>>logs/kick.err \
  || log "git fetch of the state branch failed or timed out"
LAST=$(git log -1 --format=%ct origin/state 2>>logs/kick.err)
NOW=$(date +%s)
RUN_ID=$(printf '%s' "$RUNS" | jq -r 'map(select(.status=="in_progress")) | sort_by(.createdAt) | last | .databaseId // empty')
RUN_START=$(printf '%s' "$RUNS" | jq -r 'map(select(.status=="in_progress")) | sort_by(.createdAt) | last | if . == null then 0 else (.createdAt|fromdate) end')
[ -z "$LAST" ] && { log "could not read the state branch - left $ACTIVE run(s) alone"; exit 0; }
STALE=$(( (NOW - LAST) / 60 ))

# When SHOULD this run have pushed? Not simply "7 minutes after it started":
# a session dispatched before the bell waits for the open (MAX_EARLY allows
# nearly 3 hours of it) and pushes nothing until 09:30. On 2026-10-06 that cost
# two healthy sessions - killed at 09:14 and 09:25 for being "1033m stale",
# which was merely yesterday's close. So measure from the first tick it could
# possibly have taken: whichever is later, its own start or today's open.
OPEN_EPOCH=${KICK_OPEN_EPOCH:-$(TZ=America/New_York python3 -c "import datetime; print(int(datetime.datetime.now().replace(hour=9, minute=30, second=0, microsecond=0).timestamp()))")}   # KICK_OPEN_EPOCH exists only for tests
EXPECT=$RUN_START
[ "$OPEN_EPOCH" -gt "$EXPECT" ] && EXPECT=$OPEN_EPOCH
EXPECT=$(( EXPECT + FIRST_PUSH_MIN * 60 ))
if [ -n "$RUN_ID" ] && [ "$NOW" -gt "$EXPECT" ] && [ "$STALE" -ge "$STALE_MIN" ]; then
  log "ZOMBIE: run $RUN_ID should have pushed by now but state is ${STALE}m stale -> cancel + redispatch"
  act gh run cancel "$RUN_ID" 2>>logs/kick.err
  sleep 8
  act gh workflow run session.yml 2>>logs/kick.err && log "replacement dispatched"
elif [ "$NOW" -le "$EXPECT" ]; then
  echo "$(TZ=America/New_York date '+%F %T') ET active=$ACTIVE, not due to push yet ($(( (EXPECT - NOW) / 60 ))m)" > logs/kick.last
else
  echo "$(TZ=America/New_York date '+%F %T') ET active=$ACTIVE state ${STALE}m old" > logs/kick.last
fi
