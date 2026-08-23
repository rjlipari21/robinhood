#!/usr/bin/env bash
# One scheduled trading run. Safe to invoke any time — exits quietly outside
# US market hours (9:30–16:00 ET, Mon–Fri) or when the kill switch is set.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Kill switch
if [[ -f state/HALT ]]; then
  echo "$(date -u +%FT%TZ) HALT present — skipping run"
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

mkdir -p logs state
LOG="logs/run-$(TZ=America/New_York date +%F).log"

{
  echo "===== run started $(date -u +%FT%TZ) ====="
  claude -p "$(cat prompts/trading-run.md)" \
    --output-format text \
    --max-turns 60
  echo "===== run finished $(date -u +%FT%TZ) ====="
} >> "$LOG" 2>&1
