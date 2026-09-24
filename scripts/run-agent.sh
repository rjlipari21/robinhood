#!/usr/bin/env bash
# One scheduled trading run. Safe to invoke any time — exits quietly outside
# Robinhood's regular session (09:30-16:00 ET, Mon-Fri), when a run is already
# in flight, or when the kill switch is set.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs state

# Kill switch
if [[ -f state/HALT ]]; then
  echo "$(date -u +%FT%TZ) HALT present — skipping run"
  exit 0
fi

# Overlap guard. The timer fires hourly, which is comfortably longer than an
# observed run (the three measured Haiku runs took well under 30 minutes), but
# two agents trading the same account concurrently would double-size positions
# and race on state/ledger.json, so the guard stays regardless of headroom.
# Non-blocking: if the lock is held, this fire is dropped rather than queued
# behind the run in progress.
exec {lockfd}<>state/.agent-run.lock
if ! flock -n "$lockfd"; then
  echo "$(date -u +%FT%TZ) previous run still in flight — skipping"
  exit 0
fi

# Session gate: Robinhood's REGULAR hours only, 09:30-16:00 ET Mon-Fri,
# computed in US Eastern (DST-aware via tzdata). This is the authoritative
# check -- the timer is bounded to the same window, but a manual invocation or
# a Persistent= catch-up can still land outside it.
#
# Narrowed from 07:00-20:00 (regular + extended) on 2026-09-01, together with
# a 15 -> 30 -> 60 minute cadence change the same day, to cut Claude spend:
# ~52 runs/day became 13, then 7. The tradeoff is accepted deliberately --
# nothing now manages positions between 16:00 and 09:30 the next session, a
# 17.5-hour gap where the old window left 11. Positions still gap across it
# exactly as they did overnight; what is lost is the pre-market and post-market
# chance to react first. Note the window itself is unchanged by the move to
# hourly: the last run is still 15:30 and the overnight gap is still 17.5h.
# What hourly costs is intraday resolution, not overnight coverage.
#
# Two consequences worth knowing:
#   * extended_hours is now unreachable in practice. config/limits.json still
#     accepts it and CLAUDE.md still describes when to tag it, but no run ever
#     happens outside regular hours, so every order should be regular_hours.
#   * The overnight 24 Hour Market window remains excluded and untraded:
#     config/limits.json drops all_day_hours from allowed_market_hours, so the
#     guardrail rejects any order that could fill while nothing is awake.
dow=$(TZ=America/New_York date +%u)     # 1=Mon … 7=Sun
hm=$(TZ=America/New_York date +%H%M)
open=0
if (( dow <= 5 )) && (( 10#$hm >= 930 )) && (( 10#$hm < 1600 )); then open=1; fi
if (( ! open )); then
  echo "$(date -u +%FT%TZ) outside regular hours (ET ${hm}, dow ${dow}) — skipping"
  exit 0
fi

LOG="logs/run-$(TZ=America/New_York date +%F).log"

{
  echo "===== run started $(date -u +%FT%TZ) ====="
  # Model is pinned, not left to the CLI default. The earlier estimate here
  # ($16-35/day on Sonnet 5) was wrong by ~5x. Measured from 30 real run
  # transcripts on 2026-09-01, at Sonnet 5 rates ($2/$10 per MTok in/out, cache
  # read 0.1x = $0.20, cache write 1.25x = $2.50):
  #
  #   cache reads   184.3M   $36.85   48%
  #   cache writes    8.5M   $21.13   27%
  #   output          1.9M   $19.16   25%
  #   fresh input     4.6K    $0.01    -
  #   ------------------------------------
  #   30 runs                $77.15   = $2.57/run, ~$134/day at 52 runs
  #
  # Post-pin, measured from the three Haiku 4.5 runs that executed at the
  # 30-minute cadence on 2026-09-01 (18:31, 19:01, 19:31 UTC), per run:
  #
  #   cache reads   2.50M   $0.250   41%
  #   cache writes  0.19M   $0.240   39%
  #   output        23.7K   $0.118   19%
  #   fresh input     375   $0.000    -
  #   ------------------------------------
  #   per run              ~$0.61    = ~$4.3/day at 7 runs (was ~$7.9 at 13)
  #
  # Turns on those runs were 54 / 43 / 39. The 54 would have blown the old 45
  # ceiling, so the raise to 60 was necessary rather than margin -- and 54
  # against 60 is thinner than the "should land in the 30s" note below implies.
  #
  # Two things drive that, and neither is scan ingestion alone:
  #
  # 1. Cost = turns x resident context. Cache reads dominate because every turn
  #    re-reads the whole context. The 11:07 run opened at 39.5K tokens, grew to
  #    155.2K, and ran 118 turns -- 12.4M cache reads by itself. Halving either
  #    factor halves the line.
  #
  # 2. Every run pays a cold cache. Cache TTL is 5 minutes and the cadence was
  #    15, so the ~83K-token fixed prefix (system prompt, CLAUDE.md, tool
  #    schemas) expired between every run and was never once reused: ~$0.21/run
  #    of pure re-upload. A no-op run that did nothing still cost $0.28. `claude
  #    -p` gives no way to hold a warm prefix across runs, so this is a floor
  #    until the harness changes -- and every widening of the cadence (15 -> 30
  #    -> 60) widens the gap rather than closing it. It is the price of the
  #    process model, and it does not scale down with run count: fewer runs cut
  #    the total bill but not the per-run cold-start floor.
  #
  # Haiku 4.5 is exactly half Sonnet 5's rates on all four token classes, so
  # this pin alone halves the bill. Its 200K context was previously called a
  # blocker on the strength of a 155K peak-resident figure -- but that was
  # measured pre-refactor on Sonnet 5. Measured peak on the three Haiku runs is
  # ~80K, so headroom under the 200K ceiling is roughly 60%, not 22%. Moving
  # scan ingestion VM-side is still worth doing on token cost, but the
  # context-ceiling argument for it no longer holds, and --max-turns has more
  # room to rise than the old figure suggested.
  #
  # --effort medium, not the default high: output was 25% of the bill at ~64K
  # tokens/run, most of it thinking, and most runs correctly place zero orders.
  # Raise it back to high if decision quality visibly drops.
  #
  # Caveat: Haiku 4.5 predates the effort parameter -- the API rejects
  # output_config.effort on it. The CLI flag is accepted here (verified: a test
  # run with --model claude-haiku-4-5 --effort medium exits 0), so it does not
  # break anything, but it may well be a silent no-op on this model and the
  # output-token saving should not be assumed. Verify against measured
  # output_tokens before crediting it. The flag earns its place mainly for the
  # day this pin moves back to a model that honours it.
  #
  # Pinned to an exact ID rather than the 'haiku' alias: the alias follows the
  # latest release, and a trading system should not change models silently.
  #
  # 60 turns. Was 30 (down from an original 60), then 45, which still proved
  # too tight: four runs on 2026-09-01 (11:07, 12:20, 12:52, 16:05 UTC) hit the
  # ceiling. Measured tool calls on those runs were 62-69, against a 45 ceiling.
  #
  # Raising the number is the smaller half of that fix. The larger half was
  # removing the waste, because the overrun was not analysis -- it was the same
  # boilerplate rediscovered every run:
  #   * 15-25 Bash turns rebuilding the scan union/rank pipeline by hand, with
  #     failed jq attempts and blocked /tmp writes, now one call to
  #     scripts/rank-candidates.py
  #   * 5-6 ToolSearch turns loading deferred MCP schemas piecemeal, now one
  #     batched call in step 0 of the prompt
  #   * 2-4 turns opening the run and generating ref_id UUIDs, now one call to
  #     scripts/run-context.sh
  # Together those were ~25-35 of the 62-69, so a busy run should now land in
  # the 30s and the extra headroom is margin rather than the fix itself.
  #
  # The 12:20 run is why the margin matters: it hit the ceiling four turns
  # after review_equity_order and died before place_equity_order, so a decided
  # HMY buy was simply lost. Truncation is not a clean failure mode -- the
  # PostToolUse hook writes state/ledger.json, so an order that IS placed
  # survives, but the journal is written last and holds the only record of
  # fills, reasoning and next-run watch items. This ceiling should bound
  # runaway runs only, not normal busy ones.
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
    --model claude-haiku-4-5 \
    --effort medium \
    --max-turns 60 || rc=$?

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
