#!/usr/bin/env python3
"""Hard risk guardrails for the trading agent.

Wired into Claude Code as PreToolUse/PostToolUse hooks on
mcp__robinhood-trading__place_equity_order. The model cannot bypass this:
a denied order never reaches Robinhood.

  pre  — validate the order against config/limits.json + today's ledger.
         Exit 2 (with reason on stderr) blocks the tool call.
  post — record the executed order in state/ledger.json.

Derived from TRADING_PARAMETERS.md. This hook runs offline with no broker
access, so it can only enforce what is visible in the order payload plus the
local ledger:

  ENFORCED HERE      account, side, order type (limit only), session,
                     per-order notional cap, $5 price floor, buys-per-day
                     count, HALT switch.

  AGENT JUDGMENT     position count, cash reserve, circuit breaker, settled
                     funds, averaging-down limit, all technical criteria,
                     and — importantly — the instrument-type exclusions.
                     The universe is now every US-listed common stock, so
                     nothing here can tell a stock from an ETF, ETP, or
                     closed-end fund; that screen is the agent's alone.
                     See CLAUDE.md.

Two deliberate asymmetries, both so an exit is never blocked:
  * the notional cap applies to BUYS only — a position that has grown past
    the cap must still be sellable in one order;
  * the orders-per-day cap counts BUYS only — a protective sell must never
    be refused because the day's order budget is spent.
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


def order_notional(tool_input):
    """Dollar value of the order, or None if it cannot be determined.

    Limit orders carry quantity + limit_price; dollar_amount is market-only
    but is handled so the cap cannot be sidestepped by switching order shape.
    """
    raw_dollar = tool_input.get("dollar_amount")
    if raw_dollar is not None:
        try:
            return float(raw_dollar)
        except (TypeError, ValueError):
            return None
    qty = tool_input.get("quantity")
    price = tool_input.get("limit_price")
    if qty is None or price is None:
        return None
    try:
        return float(qty) * float(price)
    except (TypeError, ValueError):
        return None


def todays_buy_count(ledger):
    today = eastern_now().strftime("%Y-%m-%d")
    return sum(
        1
        for e in ledger
        if e.get("date_et") == today and e.get("side") == "buy"
    )


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
        deny(
            f"order type {order_type!r} not allowed — "
            f"allowed: {limits['allowed_types']}"
        )

    mh = tool_input.get("market_hours") or "regular_hours"
    if mh not in limits["allowed_market_hours"]:
        deny(
            f"market_hours {mh!r} not allowed — "
            f"allowed: {limits['allowed_market_hours']}"
        )

    # Limit orders must carry a price, or the notional cap is unenforceable.
    if order_type == "limit" and tool_input.get("limit_price") is None:
        deny("limit order requires limit_price")

    if side == "buy":
        # Penny-stock floor. Only checkable on the way in: the hook cannot see
        # the live quote, so it tests the limit price the agent chose.
        try:
            price = float(tool_input.get("limit_price"))
        except (TypeError, ValueError):
            price = None
        if price is not None and price < limits["min_price_usd"]:
            deny(
                f"limit_price ${price:.2f} is below the ${limits['min_price_usd']:.2f} "
                f"minimum — no penny stocks"
            )

        notional = order_notional(tool_input)
        if notional is None:
            deny(
                "cannot determine order value — a buy needs either "
                "quantity + limit_price, or dollar_amount"
            )
        if notional <= 0:
            deny(f"order value must be positive, got ${notional:.2f}")
        if notional > limits["max_position_usd"]:
            deny(
                f"${notional:.2f} exceeds the per-position cap of "
                f"${limits['max_position_usd']:.2f}"
            )
        placed = todays_buy_count(load_ledger())
        if placed + 1 > limits["max_orders_per_day"]:
            deny(
                f"daily order cap reached — {placed} buys already placed "
                f"today, limit is {limits['max_orders_per_day']}"
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
            "market_hours": tool_input.get("market_hours"),
            "quantity": tool_input.get("quantity"),
            "limit_price": tool_input.get("limit_price"),
            "dollar_amount": tool_input.get("dollar_amount"),
            "notional_usd": order_notional(tool_input),
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
