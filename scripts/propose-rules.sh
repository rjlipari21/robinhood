#!/usr/bin/env bash
# Draft rule-change proposals from the latest weekly review, for the owner.
#
# Runs after scripts/weekly-review.py in rh-weekly-review@.service. Costs
# nothing on a quiet week: it calls Claude only when the review raised flags,
# and a flag needs at least MIN_N (10) trades in a segment, so for the first
# weeks under the 2026-10-01 rules this usually exits without a model call.
#
# The proposal run is locked down on purpose. It may only read files:
#   --tools Read           no Bash, Edit or Write
#   --strict-mcp-config    no MCP servers at all, so no Robinhood tools --
#                          it cannot see the account, let alone place orders
# It prints a markdown document; this script, not the model, appends it to
# state/proposals.md. Nothing here changes a rule. The owner approves or
# rejects in an interactive session, and only then are the rule files edited
# (see "Rule-change proposals" in CLAUDE.md).
#
# Usage: scripts/propose-rules.sh [--dry-run]   (dry run: print, don't save
#        or notify)
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1

review_json=$(ls -1 state/reviews/review-*.json 2>/dev/null | sort | tail -1)
if [[ -z "$review_json" ]]; then
  echo "propose-rules: no weekly review yet -- nothing to do"; exit 0
fi
date=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['date'])" "$review_json")
nflags=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['flags']))" "$review_json")

if (( nflags == 0 )); then
  echo "propose-rules: review $date raised no flags -- no proposals, no model call"; exit 0
fi
if (( ! DRY )) && grep -q "^## Proposals from the $date review" state/proposals.md 2>/dev/null; then
  echo "propose-rules: proposals for $date already drafted -- skipping"; exit 0
fi

prompt="$(sed "s/DATE/$date/g" prompts/propose-rules.md)

The review to work from is state/reviews/review-$date.md ($nflags flag(s))."

out=$(timeout 15m claude -p "$prompt" \
  --tools Read \
  --strict-mcp-config \
  --model claude-sonnet-5 \
  --max-turns 30 \
  --output-format text 2>&1)
rc=$?

# Accept only a document in the required shape; anything else (an error, a
# preamble, a truncated run) is logged and dropped rather than saved as a
# "proposal" the owner might act on.
if (( rc != 0 )) || [[ "$(printf '%s\n' "$out" | head -1)" != "## Proposals from the $date review" ]]; then
  echo "propose-rules: proposal run failed or malformed (rc=$rc); nothing saved. First lines:"
  printf '%s\n' "$out" | head -5
  exit 1
fi

if (( DRY )); then
  printf '%s\n' "$out"; exit 0
fi

{
  [[ -s state/proposals.md ]] || printf '# Rule-change proposals\n\nDrafted by scripts/propose-rules.sh from the weekly reviews. Nothing here is in\neffect until the owner approves it and the rule files are edited.\n'
  printf '\n%s\n' "$out"
} >> state/proposals.md

n=$(printf '%s\n' "$out" | grep -c '^### P-')
echo "propose-rules: $n proposal(s) from the $date review appended to state/proposals.md"
if (( n > 0 )); then
  python3 hooks/notify.py summary "Rule-change proposals ($n)" \
    "The $date weekly review produced $n proposal(s). Nothing changes until you approve. Review in an interactive Claude session on the VM: state/proposals.md" || true
fi
