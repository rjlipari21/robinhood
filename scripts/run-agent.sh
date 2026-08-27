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
# The overnight 24 Hour Market window (20:00-07:00 ET) is deliberately excluded,
# and since 2026-08-27 it is not traded at all: config/limits.json drops
# all_day_hours from allowed_market_hours, so the guardrail rejects any order
# that could fill inside the unmonitored window. Without that, an order tagged
# all_day_hours near the 20:00 close could fill at 03:00 and sit unmanaged with
# no protective exit until 07:00.
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
  # 45 turns. Was 30 (down from an original 60), which proved too tight on
  # 2026-08-25: the 15:00 ET run placed a TXT exit, then hit the ceiling before
  # writing its journal entry, so a real fill went unrecorded and the next run
  # would have read a journal claiming the position was still open.
  #
  # The 20-25 turn estimate behind the 30 ceiling was measured on zero-order
  # runs. A run that manages an exit AND screens for entries with deployable
  # cash costs materially more: each position exit is a technicals read plus a
  # review/place pair, and the entry search that follows starts from scratch.
  #
  # Truncation is not a clean failure mode. The PostToolUse hook writes
  # state/ledger.json, so the order record survives -- but the journal is the
  # only place fills, reasoning, and next-run watch items are recorded, and it
  # is written last. Losing it is worse than the cost of the extra turns, so
  # this ceiling should bound runaway runs only, not normal busy ones.
  # `|| rc=$?` rather than a bare call: set -e is on, so an agent crash or a
  # non-zero exit would otherwise abort the script here and skip the fill drain
  # below -- exactly the run where a recorded fill most needs reporting.
  rc=0
  claude -p "$(cat prompts/trading-run.md)" \
    --output-format text \
    --model claude-sonnet-5 \
    --max-turns 45 || rc=$?

  # Fill alerts. The agent reconciles get_equity_orders every run and appends
  # each newly-observed fill to state/fills.jsonl; this drains that into ntfy.
  #
  # Deliberately OUTSIDE the claude call and after it, so a run that dies or
  # truncates still gets its already-recorded fills reported. Dedupe is by
  # order_id in state/fills-notified.json, so running it every time is safe.
  #
  # `|| true` because an alerting failure must never fail a trading run --
  # notify.py already exits 0 on every internal error, this covers the rest.
  python3 hooks/notify.py fills || true

  echo "===== run finished $(date -u +%FT%TZ) rc=$rc ====="
} >> "$LOG" 2>&1
