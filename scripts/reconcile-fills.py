#!/usr/bin/env python3
"""Backstop for fill alerts: add any fill the agent did not record.

The agent appends each new fill to state/fills.jsonl in step 9 of the run
prompt, and run-agent.sh then drains that file to the owner's phone. When the
agent misses one, the owner never hears about a trade: the ABNB sell on
2026-09-03 never reached fills.jsonl, and two buys in late August were only
recorded a run late. This runs after the agent exits, before the drain, and
asks Robinhood directly which orders actually traded.

A "fill" here is any order with shares executed: state `filled`, or
`cancelled` with cumulative_quantity > 0 -- a gfd limit that part-filled and
expired at the close still moved money. `partially_filled` orders are left to
a later run, because fills.jsonl alerts once per order_id and alerting on a
partial would swallow the alert for the completed fill.

Only orders created in the last LOOKBACK_DAYS are considered, so the first run
does not text about month-old trades. Lines written here carry a `note`
saying so; count those notes to see how often the agent misses a fill.

Appends only and never rewrites, same contract as the agent. Talks to
Robinhood exactly the way scripts/news-brief.py does and reuses its plumbing.
Failure is soft: it prints one line and exits non-zero, and run-agent.sh
carries on to the drain regardless.

Exit codes: 0 done | 3 credential | 4 transport | 5 tool error | 6 bad data
"""
import datetime as dt
import importlib.util
import json
import os
import sys

ACCOUNT = "797887684"
LOOKBACK_DAYS = 7
BUY_LOOKBACK_DAYS = 60          # to find the entry price behind a sell
MAX_PAGES = 10
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILLS = os.path.join(ROOT, "state", "fills.jsonl")

_spec = importlib.util.spec_from_file_location(
    "news_brief", os.path.join(ROOT, "scripts", "news-brief.py"))
nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nb)


def die(code, msg):
    print(f"reconcile-fills: {msg}")
    sys.exit(code)


nb.die = die


def tool_data(result):
    for b in (result or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text":
            try:
                d = json.loads(b.get("text") or "")
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and isinstance(d.get("data"), dict):
                return d["data"]
    sc = (result or {}).get("structuredContent") or {}
    if isinstance(sc.get("data"), dict):
        return sc["data"]
    die(5, "no data envelope in tool result")


class Client:
    def __init__(self):
        self.token = nb.access_token()
        _, hdrs = nb.rpc(self.token, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "reconcile-fills", "version": "1.0"},
        }, 1, None)
        self.session = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
        nb.rpc(self.token, "notifications/initialized", {}, None, self.session)
        self.next_id = 10

    def orders(self, state, since):
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            args = {"account_number": ACCOUNT, "state": state,
                    "created_at_gte": since}
            if cursor:
                args["cursor"] = cursor
            self.next_id += 1
            res, _ = nb.rpc(self.token, "tools/call", {
                "name": "get_equity_orders", "arguments": args,
            }, self.next_id, self.session)
            data = tool_data(res)
            out.extend(data.get("orders") or [])
            cursor = data.get("next")
            if not cursor:
                break
        return out


def recorded_ids():
    ids = set()
    try:
        with open(FILLS) as f:
            for line in f:
                try:
                    ids.add(str(json.loads(line)["order_id"]))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return ids


def executed_qty(o):
    try:
        return float(o.get("cumulative_quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def filled_at(o):
    ts = [e.get("timestamp") for e in o.get("executions") or [] if e.get("timestamp")]
    return max(ts) if ts else o.get("last_transaction_at")


def main():
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    buy_since = (now - dt.timedelta(days=BUY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    c = Client()
    traded = [o for o in c.orders("filled", since) + c.orders("cancelled", since)
              if executed_qty(o) > 0]
    have = recorded_ids()
    missing = [o for o in traded if str(o.get("id")) not in have]
    if not missing:
        print(f"reconcile-fills: all {len(traded)} fill(s) since {since} already recorded")
        return 0

    # Entry price for a sell's P/L: the latest filled buy of that symbol
    # before the sell. Approximate when a position was built in several buys,
    # which the note says.
    buys = None
    if any(o.get("side") == "sell" for o in missing):
        buys = [o for o in c.orders("filled", buy_since) if o.get("side") == "buy"]

    lines = []
    for o in sorted(missing, key=lambda o: str(filled_at(o))):
        try:
            avg = float(o["average_price"])
        except (KeyError, TypeError, ValueError):
            die(6, f"order {o.get('id')} has no usable average_price")
        rec = {
            "order_id": o["id"],
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "quantity": executed_qty(o),
            "average_price": avg,
            "filled_at": filled_at(o),
        }
        note = "Recorded by reconcile-fills.py after the run: the agent did not record this fill"
        if o.get("state") == "cancelled":
            note += f"; partial fill, order then cancelled ({executed_qty(o):g} of {o.get('quantity')} shares)"
        if o.get("side") == "sell" and buys:
            prior = [b for b in buys if b.get("symbol") == o.get("symbol")
                     and str(filled_at(b)) < str(filled_at(o))]
            if prior:
                entry = float(max(prior, key=lambda b: str(filled_at(b)))["average_price"])
                rec["pnl_pct"] = round((avg / entry - 1) * 100, 2)
                note += f"; P/L vs last buy at {entry:g}"
        rec["note"] = note
        lines.append(json.dumps(rec, separators=(",", ":")))

    os.makedirs(os.path.dirname(FILLS), exist_ok=True)
    with open(FILLS, "a") as f:
        f.write("".join(l + "\n" for l in lines))
    for o in missing:
        print(f"reconcile-fills: recorded missed fill {o.get('symbol')} "
              f"{o.get('side')} {executed_qty(o):g} order {o.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
