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

# Market-hours gate, computed in US Eastern (DST-aware via tzdata)
dow=$(TZ=America/New_York date +%u)     # 1=Mon … 7=Sun
hm=$(TZ=America/New_York date +%H%M)
if (( dow > 5 )) || (( 10#$hm < 930 )) || (( 10#$hm >= 1600 )); then
  echo "$(date -u +%FT%TZ) outside market hours (ET ${hm}, dow ${dow}) — skipping"
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
