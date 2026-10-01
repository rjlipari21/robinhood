#!/usr/bin/env python3
"""Build state/trades.jsonl: one line per trade, for learning from results.

Why: the 2026-10-01 performance review had to rebuild every trade by hand
from fills.jsonl and journal prose, and one sale (ABNB, 09-03) was missing
entirely. Judging a rule -- dip vs momentum entries, which exit fires, whether
analyst consensus or the market filter matters -- needs each trade as a row of
data with its context at entry. This is that table.

Runs after every agent run (run-agent.sh, after reconcile-fills.py). Two jobs:

1. CAPTURE context for new buys, once, close to entry time: the SPY market
   verdict (scripts/market-regime.py) and Wall Street analyst ratings
   (buy/hold/sell counts, price targets). Stored in state/trade-context.json
   keyed by the buy's order_id, so it is never re-fetched or overwritten --
   ratings fetched a week later would describe a different moment. Buys
   before CONTEXT_SINCE get none: fetching today's ratings for a September
   entry would be misleading, so those trades say "not recorded".

2. REBUILD state/trades.jsonl from state/fills.jsonl plus that context,
   pairing buys and sells per symbol first-in first-out. The file is derived
   and rewritten whole each run (atomically); fills.jsonl and
   trade-context.json are the sources and are only ever appended to.

Entry signals (path, hourly RSI, relative volume, breakout level) and the exit
reason come from fields the agent writes on its fills.jsonl lines (step 9 of
the run prompt). `model` is the Claude model the trading run was pinned to
when the trade opened: MODEL_HISTORY below (from git history) plus any later
changes run-agent.sh appends to state/model-history.jsonl. Older lines predate those fields, so exit_reason is inferred
from the note text and marked `exit_reason_source: inferred`.

Failure is soft: one line, non-zero exit; the run is unaffected. If the
context fetch fails, the rebuild still happens and the capture is retried
next run (as long as the buy is recent enough that ratings still fit).

Exit codes: 0 done | 3 credential | 4 transport | 5 tool error
"""
import datetime as dt
import importlib.util
import json
import os
import sys
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILLS = os.path.join(ROOT, "state", "fills.jsonl")
CONTEXT = os.path.join(ROOT, "state", "trade-context.json")
TRADES = os.path.join(ROOT, "state", "trades.jsonl")
MODELS = os.path.join(ROOT, "state", "model-history.jsonl")
CONTEXT_SINCE = "2026-10-01"   # first day the agent records entry context
MAX_CAPTURE_AGE_H = 24         # past this, ratings no longer describe entry
# Model pins before run-agent.sh logged them, dated by the commit that set each
# (34d4c14, d398da8). Before the first pin the CLI default was used.
MODEL_HISTORY = [
    ("1970-01-01T00:00:00Z", "cli-default"),
    ("2026-08-25T01:06:01Z", "claude-sonnet-5"),
    ("2026-09-01T19:51:03Z", "claude-haiku-4-5"),
]

_spec = importlib.util.spec_from_file_location(
    "market_regime", os.path.join(ROOT, "scripts", "market-regime.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)
nb = mr.nb


class FetchFailed(Exception):
    pass


def _raise(code, msg):
    raise FetchFailed(msg)


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_fills():
    """Unique fills by order_id, first line wins (the agent's own, richer)."""
    seen, out = set(), []
    try:
        with open(FILLS) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                oid = str(d.get("order_id"))
                if oid in seen or not d.get("symbol"):
                    continue
                seen.add(oid)
                out.append(d)
    except FileNotFoundError:
        pass
    return sorted(out, key=lambda d: str(d.get("filled_at")))


def load_context():
    try:
        with open(CONTEXT) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def load_models():
    """[(since, model)] oldest first: the seed plus run-agent.sh's log."""
    hist = list(MODEL_HISTORY)
    try:
        with open(MODELS) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                    hist.append((str(d["since"]), str(d["model"])))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return sorted(hist)


def model_at(ts, hist):
    t = parse_ts(ts)
    if not t:
        return "unknown"
    out = "unknown"
    for since, model in hist:
        s = parse_ts(since)
        if s and s <= t:
            out = model
    return out


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def capture(buys, context):
    """Fetch market verdict + analyst ratings for buys lacking context."""
    now = dt.datetime.now(dt.timezone.utc)
    todo = []
    for b in buys:
        oid = str(b["order_id"])
        ts = parse_ts(b.get("filled_at"))
        if oid in context or not ts or str(b.get("filled_at")) < CONTEXT_SINCE:
            continue
        if (now - ts).total_seconds() > MAX_CAPTURE_AGE_H * 3600:
            context[oid] = {"captured_at": None,
                            "skipped": f"not captured within {MAX_CAPTURE_AGE_H}h of the fill"}
            continue
        todo.append(b)
    if not todo:
        return 0

    mr.die = nb.die = _raise          # fail this step, not the whole script
    token, session = mr.connect()
    market = mr.regime(token, session, rpc_id=10)
    syms = sorted({b["symbol"] for b in todo})
    res, _ = nb.rpc(token, "tools/call", {
        "name": "get_equity_analyst_ratings", "arguments": {"symbols": syms},
    }, 20, session)
    ratings = {}
    for r in mr.tool_data(res).get("results") or []:
        ratings[r.get("symbol")] = r.get("ratings")

    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for b in todo:
        price = f(b.get("average_price"))
        rt = ratings.get(b["symbol"])
        analyst = None
        if rt:
            nbuy, nhold, nsell = (int(rt.get(k) or 0) for k in
                                  ("num_buy_ratings", "num_hold_ratings", "num_sell_ratings"))
            mean = f(rt.get("mean_price_target"))
            analyst = {
                "buy": nbuy, "hold": nhold, "sell": nsell,
                "buy_pct": round(100 * nbuy / (nbuy + nhold + nsell), 1) if nbuy + nhold + nsell else None,
                "target_low": f(rt.get("low_price_target")),
                "target_mean": mean,
                "target_high": f(rt.get("high_price_target")),
                "upside_pct": round((mean / price - 1) * 100, 2) if mean and price else None,
            }
        context[str(b["order_id"])] = {
            "captured_at": stamp, "market": market,
            "analyst": analyst if rt else "no coverage",
        }
    return len(todo)


EXIT_HINTS = [   # first match wins; order matters
    ("earnings", "earnings"), ("protective", "protective"),
    ("trend", "trend-down"), ("micro", "trend-down"),
    ("ladder", "rsi"), ("rsi", "rsi"),
    ("range", "range-top"), ("profit", "profit-target"), ("target", "profit-target"),
]


def exit_reason(fill):
    if fill.get("exit_reason"):
        return fill["exit_reason"], "agent"
    note = str(fill.get("note") or "").lower()
    # The agent's notes lead with a label ("Profit-target exit: ..."), and the
    # rest often mentions other triggers in passing, so try the label first.
    for text in (note.split(":", 1)[0], note):
        for hint, reason in EXIT_HINTS:
            if hint in text:
                return reason, "inferred"
    return "unknown", "inferred"


def build(fills, context, models):
    lots = defaultdict(deque)       # symbol -> open trades, oldest first
    trades = []

    def open_trade(b, qty, price, ts, orphan=False):
        ctx = context.get(str(b.get("order_id"))) if not orphan else None
        t = {
            "trade_id": str(b.get("order_id")) if not orphan else f"orphan-{b.get('order_id')}",
            "symbol": b.get("symbol"),
            "status": "open",
            "entry_path": b.get("entry_path") or ("unknown" if not orphan else "pre-agent"),
            "model": "pre-agent" if orphan else model_at(ts, models),
            "opened_at": ts,
            "entry_price": price,
            "qty": qty,
            "notional": round(qty * price, 2) if price else None,
            "entry_rsi_1h": f(b.get("rsi_1h")),
            "entry_relvol": f(b.get("relvol")),
            "breakout_level": f(b.get("breakout_level")),
            "entry_note": b.get("note"),
            "context": ctx if ctx else (
                "position opened before this account's fills were tracked" if orphan else
                f"not recorded (entry before {CONTEXT_SINCE})"),
            "remaining": qty, "exits": [],
        }
        trades.append(t)
        return t

    for fl in fills:
        sym, side = fl["symbol"], fl.get("side")
        qty, price, ts = f(fl.get("quantity")) or 0, f(fl.get("average_price")), fl.get("filled_at")
        if side == "buy":
            lots[sym].append(open_trade(fl, qty, price, ts))
            continue
        left = qty
        while left > 1e-9:
            if not lots[sym]:
                # A sale with no recorded buy: a position from before fills were
                # tracked (the owner's opening positions on 2026-08-25). Recover
                # the entry from the agent's recorded P/L when it has one.
                pnl = f(fl.get("pnl_pct"))
                entry = price / (1 + pnl / 100) if price and pnl is not None else None
                lots[sym].append(open_trade(fl, left, entry, None, orphan=True))
            t = lots[sym][0]
            take = min(left, t["remaining"])
            reason, src = exit_reason(fl)
            t["exits"].append({"order_id": fl.get("order_id"), "at": ts, "qty": take,
                               "price": price, "reason": reason, "reason_source": src})
            t["remaining"] -= take
            left -= take
            if t["remaining"] <= 1e-9:
                lots[sym].popleft()

    for t in trades:
        if t["remaining"] > 1e-9:
            continue
        t["status"] = "closed"
        sold = sum(e["qty"] for e in t["exits"])
        exit_px = sum(e["qty"] * (e["price"] or 0) for e in t["exits"]) / sold if sold else None
        t["exit_price"] = round(exit_px, 4) if exit_px else None
        t["closed_at"] = t["exits"][-1]["at"]
        t["exit_reason"] = t["exits"][-1]["reason"]
        t["exit_reason_source"] = t["exits"][-1]["reason_source"]
        if t["entry_price"] and exit_px:
            t["pnl_usd"] = round((exit_px - t["entry_price"]) * t["qty"], 2)
            t["pnl_pct"] = round((exit_px / t["entry_price"] - 1) * 100, 2)
        a, b = parse_ts(t["opened_at"]), parse_ts(t["closed_at"])
        if a and b:
            t["hold_hours"] = round((b - a).total_seconds() / 3600, 1)
    for t in trades:
        del t["remaining"]
    return trades


def main():
    fills = load_fills()
    context = load_context()
    note = ""
    try:
        n = capture([x for x in fills if x.get("side") == "buy"], context)
        if n:
            save_json(CONTEXT, context)
            note = f", captured entry context for {n} new buy(s)"
    except FetchFailed as exc:
        note = f", context capture FAILED ({exc}) -- retried next run"
    trades = build(fills, context, load_models())
    tmp = TRADES + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("".join(json.dumps(t, separators=(",", ":")) + "\n" for t in trades))
    os.replace(tmp, TRADES)
    closed = [t for t in trades if t["status"] == "closed"]
    print(f"trade-record: {len(closed)} closed, {len(trades) - len(closed)} open{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
