#!/usr/bin/env bash
# End-to-end check of the dashboard: data layer, HTTP surface, and client
# rendering. Read-only -- safe to run against the live account at any time.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SNAP=/tmp/dashboard-snapshot.json
PORT=${PORT:-8098}

echo "== 1. data layer =="
python3 -c "
import sys, json; sys.path.insert(0,'webapp')
import collect
json.dump(collect.snapshot(7), open('$SNAP','w'), default=str)
d = json.load(open('$SNAP'))
print(f\"   {len(d['positions'])} positions, {len(d['journal'])} journal entries, \"
      f\"{len(d['equity_curve'])} curve points, live_error={d['live_error']}\")
assert 'accessToken' not in json.dumps(d), 'CREDENTIAL LEAKED INTO SNAPSHOT'
print('   no credential material in the payload')

dec = d['decisions']
print(f\"   {len(dec['orders'])} orders attributed to {len(dec['coverage'])} data \"
      f\"sources, {dec['unmatched_runs']} with no journal entry to match\")

# A citation whose quote does not contain the phrase that matched is evidence
# for nothing -- it happened once, by trimming long journal lines from the
# start instead of windowing on the match.
bad = [(o['symbol'], c['id']) for o in dec['orders'] for c in o['cited']
       if c['tier'] != 'ledger' and not collect._SRC_RE[c['id']].search(c['quote'])]
assert not bad, f'quote does not contain its own matched phrase: {bad[:3]}'
print('   every citation quotes the phrase that evidenced it')

# The page states the caps; limits.json is what the hook enforces.
lim = json.load(open('config/limits.json'))
assert d['guidelines']['limits']['max_position_usd'] == lim['max_position_usd']
assert d['guidelines']['limits']['max_positions'] == lim['max_positions']
print('   guideline caps come from config/limits.json')

# The source descriptions quote a few of those caps in prose, where an
# f-string would read worse. This is what keeps the prose honest.
prose = ' '.join(c['provides'] + ' ' + c['feeds'] for c in dec['coverage'])
for claim in (f\"{lim['max_positions']}-position ceiling\",
              f\"{lim['min_cash_reserve_pct']:.0f}% cash reserve\",
              f\"{lim['min_price_usd']:.0f} price floor\"):
    assert claim in prose, f'source prose no longer matches limits.json: {claim}'
print('   source descriptions quote the same caps')

assert 'now_et' not in d['status'] and d['generated_at'].endswith('PT')
print('   timestamps emitted in Pacific')
"

echo "== 2. http surface =="
python3 webapp/server.py --port "$PORT" >/tmp/dash-test.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for _ in $(seq 20); do curl -fsS "localhost:$PORT/healthz" >/dev/null 2>&1 && break; sleep .3; done

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
[[ "$(code "localhost:$PORT/")" == 200 ]]            && echo "   GET /            200"
[[ "$(code "localhost:$PORT/api/snapshot")" == 200 ]] && echo "   GET /api/snapshot 200"
[[ "$(code "localhost:$PORT/nope")" == 404 ]]         && echo "   GET /nope        404"
[[ "$(code -X POST "localhost:$PORT/")" == 405 ]]     && echo "   POST /           405 (read-only)"
curl -sI "localhost:$PORT/" | grep -qi 'content-security-policy' && echo "   CSP header present"

echo "== 3. client rendering =="
node webapp/test-render.mjs "$SNAP"
