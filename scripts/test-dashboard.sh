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
