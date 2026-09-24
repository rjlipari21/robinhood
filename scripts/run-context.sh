#!/usr/bin/env bash
# Steps 1-2 of a run in a single call: kill switch, journal tail, ledger tail,
# and a batch of ref_id UUIDs.
#
# Exists to be allowlistable. The agent used to open a run with an ad-hoc
# compound command (echo && ls && tail ...), which cannot be matched by a
# permissions rule, and to generate ref_ids by trial and error -- on
# 2026-09-01 the 12:20 run spent its last four turns failing to produce a UUID
# and died after review_equity_order but before place_equity_order, losing a
# decided trade. Fixed commands here mean fixed rules in settings.json.
#
# Deliberately NOT a general file reader: it takes no arguments and touches
# only known state/ paths, so allowlisting it cannot become a way to read
# ~/.ssh or .env, which settings.json denies to the Read tool.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=== HALT ==="
if [[ -f state/HALT ]]; then
  echo "PRESENT -- kill switch is set. Stop the run immediately, place no orders."
else
  echo "absent"
fi

echo
echo "=== date ==="
echo "ET now: $(TZ=America/New_York date '+%F %H:%M %Z (%a)')"

echo
echo "=== journal tail (last 200 lines of state/journal.md) ==="
# The journal is the durable narrative and is now >1MB, so never read it whole.
tail -n 200 state/journal.md 2>/dev/null || echo "(no journal yet)"

echo
echo "=== ledger tail (last 15 executed orders) ==="
# Hook-maintained; read-only here.
python3 - <<'PY' 2>/dev/null || echo "(no ledger yet)"
import json
try:
    d = json.load(open("state/ledger.json"))
except Exception as e:
    raise SystemExit(f"(ledger unreadable: {e})")
orders = d if isinstance(d, list) else d.get("orders", [])
for o in orders[-15:]:
    print(json.dumps(o, separators=(",", ":")))
PY

echo
echo "=== fills.jsonl order_ids already alerted (do not re-add these) ==="
python3 -c "
import json
seen=[]
try:
    for line in open('state/fills.jsonl'):
        line=line.strip()
        if not line: continue
        try: seen.append(json.loads(line)['order_id'])
        except Exception: pass
except FileNotFoundError: pass
print(' '.join(seen[-40:]) or '(none)')
print(f'({len(seen)} total lines)')
"

echo
echo "=== fresh ref_id UUIDs (use each at most once this run) ==="
for _ in 1 2 3 4 5 6; do cat /proc/sys/kernel/random/uuid; done
