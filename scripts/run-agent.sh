#!/usr/bin/env bash
# One scheduled trading run. Safe to invoke any time — exits quietly outside
# Robinhood's regular/extended sessions (07:00-20:00 ET, Mon-Fri), when a run
# is already in flight, or when the kill switch is set.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs state

# Kill switch
if [[ -f state/HALT ]]; then
  echo "$(date -u +%FT%TZ) HALT present — skipping run"
  exit 0
fi

# Overlap guard. The timer fires every 15 minutes but a run can take longer than
# that, and two agents trading the same account concurrently would double-size
# positions and race on state/ledger.json. Non-blocking: if the lock is held,
# this fire is dropped rather than queued behind the run in progress.
exec {lockfd}<>state/.agent-run.lock
if ! flock -n "$lockfd"; then
  echo "$(date -u +%FT%TZ) previous run still in flight — skipping"
  exit 0
fi

# Session gate: Robinhood's regular + extended hours only, 07:00-20:00 ET
# Mon-Fri, computed in US Eastern (DST-aware via tzdata). This is the
# authoritative check -- the timer is bounded to the same window, but a manual
# invocation or a Persistent= catch-up can still land outside it.
#
# The overnight 24 Hour Market window (20:00-07:00 ET) is deliberately excluded.
# Consequence to keep in mind: an order tagged all_day_hours placed near the
# 20:00 close can still fill overnight, and no run will manage the position
# until 07:00 -- roughly an 11-hour unmonitored window with no protective exit.
dow=$(TZ=America/New_York date +%u)     # 1=Mon … 7=Sun
hm=$(TZ=America/New_York date +%H%M)
open=0
if (( dow <= 5 )) && (( 10#$hm >= 700 )) && (( 10#$hm < 2000 )); then open=1; fi
if (( ! open )); then
  echo "$(date -u +%FT%TZ) outside regular/extended hours (ET ${hm}, dow ${dow}) — skipping"
  exit 0
fi

LOG="logs/run-$(TZ=America/New_York date +%F).log"

{
  echo "===== run started $(date -u +%FT%TZ) ====="
  # Model is pinned, not left to the CLI default. Measured against this repo's
  # own session history (95.8% cache reads, 3.3% cache writes, 0.9% output) plus
  # the live scan payloads, the 15-minute session-bounded cadence runs ~45-95M
  # tokens/day across ~52 runs -- about $16-35/day on Sonnet 5, $25-52 once its
  # intro pricing ends. The Opus default would be roughly 3x that.
  #
  # The dominant term is scan ingestion, not turn count: both saved scans total
  # ~36K tokens (broad 200 rows ~21K, low-price 121 rows ~15K) and they sit in
  # context for the rest of the run, so a 25-turn run re-reads them ~25 times as
  # cache reads. Trimming scan columns cuts cost faster than trimming cadence.
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
