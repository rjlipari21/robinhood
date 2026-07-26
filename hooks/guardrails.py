#!/usr/bin/env python3
"""Hard risk guardrails for the trading agent.

Wired into Claude Code as PreToolUse/PostToolUse hooks on
mcp__robinhood-trading__place_equity_order. The model cannot bypass this:
a denied order never reaches Robinhood.

  pre  — validate the order against config/limits.json + today's ledger.
         Exit 2 (with reason on stderr) blocks the tool call.
  post — record the executed order in state/ledger.json.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMITS_PATH = os.path.join(ROOT, "config", "limits.json")
LEDGER_PATH = os.path.join(ROOT, "state", "ledger.json")
HALT_PATH = os.path.join(ROOT, "state", "HALT")

# US Eastern without pytz: UTC-5, or UTC-4 during DST (second Sunday of March
# to first Sunday of November — accurate for current US rules).
def eastern_now():
    utc = datetime.now(timezone.utc)
    year = utc.year
    def nth_sunday(month, n):
        d = datetime(year, month, 1, tzinfo=timezone.utc)
        days_to_sun = (6 - d.weekday()) % 7
        return d + timedelta(days=days_to_sun + 7 * (n - 1))
    dst_start = nth_sunday(3, 2).replace(hour=7)   # 2am ET = 7am UTC (EST)
    dst_end = nth_sunday(11, 1).replace(hour=6)    # 2am ET = 6am UTC (EDT)
    offset = -4 if dst_start <= utc < dst_end else -5
    return utc + timedelta(hours=offset)


def deny(reason):
    print(f"ORDER BLOCKED by guardrails: {reason}", file=sys.stderr)
    sys.exit(2)


def load_ledger():
    try:
        with open(LEDGER_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def todays_buy_total(ledger):
    today = eastern_now().strftime("%Y-%m-%d")
    total = 0.0
    for entry in ledger:
        if entry.get("date_et") == today and entry.get("side") == "buy":
            total += float(entry.get("dollar_amount") or 0)
    return total


def pre(payload):
    if os.path.exists(HALT_PATH):
        deny("HALT file present — trading is disabled by the owner")

    with open(LIMITS_PATH) as f:
        limits = json.load(f)
    tool_input = payload.get("tool_input") or {}

    account = tool_input.get("account_number")
    if account != limits["account_number"]:
        deny(f"account {account!r} is not the authorized agentic account")

    side = tool_input.get("side")
    if side not in limits["allowed_sides"]:
        deny(f"side {side!r} not allowed")

    order_type = tool_input.get("type")
    if order_type not in limits["allowed_types"]:
        deny(f"order type {order_type!r} not allowed")

    mh = tool_input.get("market_hours") or "regular_hours"
    if mh != "regular_hours":
        deny(f"market_hours {mh!r} not allowed — regular_hours only")

    if side == "buy":
        if tool_input.get("quantity") is not None:
            deny("buys must use dollar_amount, not quantity")
        raw = tool_input.get("dollar_amount")
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            deny(f"buy requires a numeric dollar_amount, got {raw!r}")
        if amount <= 0:
            deny("dollar_amount must be positive")
        if amount > limits["per_trade_usd"]:
            deny(
                f"${amount:.2f} exceeds the per-trade cap of "
                f"${limits['per_trade_usd']:.2f}"
            )
        spent = todays_buy_total(load_ledger())
        if spent + amount > limits["daily_buy_usd"]:
            deny(
                f"daily buy cap ${limits['daily_buy_usd']:.2f} would be "
                f"exceeded (already committed ${spent:.2f} today). "
                f"No more buys today."
            )
    sys.exit(0)


def post(payload):
    tool_input = payload.get("tool_input") or {}
    ledger = load_ledger()
    ledger.append(
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "date_et": eastern_now().strftime("%Y-%m-%d"),
            "symbol": tool_input.get("symbol"),
            "side": tool_input.get("side"),
            "type": tool_input.get("type"),
            "dollar_amount": tool_input.get("dollar_amount"),
            "quantity": tool_input.get("quantity"),
            "limit_price": tool_input.get("limit_price"),
            "ref_id": tool_input.get("ref_id"),
        }
    )
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2)
    sys.exit(0)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    if mode == "pre":
        pre(payload)
    elif mode == "post":
        post(payload)
    else:
        print("usage: guardrails.py pre|post", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
