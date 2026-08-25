#!/usr/bin/env bash
# One scheduled trading run. Safe to invoke any time — exits quietly when the
# 24 Hour Market is fully closed (Fri 20:00 ET -> Sun 20:00 ET), when a run is
# already in flight, or when the kill switch is set.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs state

# Kill switch
if [[ -f state/HALT ]]; then
  echo "$(date -u +%FT%TZ) HALT present — skipping run"
  exit 0
fi

# Overlap guard. The timer fires every minute but a run can take longer than
# that, and two agents trading the same account concurrently would double-size
# positions and race on state/ledger.json. Non-blocking: if the lock is held,
# this fire is dropped rather than queued behind the run in progress.
exec {lockfd}<>state/.agent-run.lock
if ! flock -n "$lockfd"; then
  echo "$(date -u +%FT%TZ) previous run still in flight — skipping"
  exit 0
fi

# Session gate for the 24 Hour Market, computed in US Eastern (DST-aware via
# tzdata). It opens Sunday 20:00 ET and runs continuously to Friday 20:00 ET;
# the only fully-closed window is Fri 20:00 -> Sun 20:00. Entries use
# all_day_hours, so runs are useful overnight — this gate must not narrow to
# regular hours or the strategy loses most of its fill window.
dow=$(TZ=America/New_York date +%u)     # 1=Mon … 7=Sun
hm=$(TZ=America/New_York date +%H%M)
closed=0
if   (( dow == 6 )); then closed=1                                   # all Saturday
elif (( dow == 7 )) && (( 10#$hm < 2000 )); then closed=1            # Sun before 20:00
elif (( dow == 5 )) && (( 10#$hm >= 2000 )); then closed=1           # Fri after 20:00
fi
if (( closed )); then
  echo "$(date -u +%FT%TZ) market fully closed (ET ${hm}, dow ${dow}) — skipping"
  exit 0
fi

LOG="logs/run-$(TZ=America/New_York date +%F).log"

{
  echo "===== run started $(date -u +%FT%TZ) ====="
  # Model is pinned, not left to the CLI default. Measured against this repo's
  # own session history (95.8% cache reads, 3.3% cache writes, 0.9% output), a
  # one-minute cadence runs ~150-400M tokens/day; on the Opus default that is
  # $140-365/day against a ~$1,000 account. Sonnet 5 cuts that ~60-75%.
  #
  # Pinned to an exact ID rather than the 'sonnet' alias: the alias follows the
  # latest release, and a trading system should not change models silently.
  #
  # Haiku 4.5 would be cheaper again, but its 200K context is a real ceiling
  # here -- a heavy run stacks scan output (up to 200 rows) plus per-candidate
  # technicals across 30 turns, and truncating a run mid-decision is worse than
  # the price difference. Sonnet 5 has the full 1M window.
  #
  # 30 turns, down from 60: a normal run is journal read + scan + technicals on
  # a handful of names + review/place pairs + journal write, which lands around
  # 20-25. The old ceiling mostly bounded runaway runs, and each extra turn
  # re-reads the whole history. Truncation loses the journal narrative, not the
  # order record -- the PostToolUse hook writes state/ledger.json, not the agent.
  claude -p "$(cat prompts/trading-run.md)" \
    --output-format text \
    --model claude-sonnet-5 \
    --max-turns 30
  echo "===== run finished $(date -u +%FT%TZ) ====="
} >> "$LOG" 2>&1
