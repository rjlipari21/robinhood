#!/usr/bin/env python3
"""Market filter: may this run open new positions, judged by SPY?

Rule (TRADING_PARAMETERS.md, owner instruction 2026-10-01): no new buys while
SPY is down 1% or more on the day. The 20-day-average condition it started
with is switched off (USE_SMA_RULE) but still computed and reported.
Exits are never affected -- this gates entries only.

Why: a pullback in a single name is the setup this strategy wants; a pullback
that is just the whole market falling is not. Checked against SPY daily
closes before adoption: the rule would have blocked the 2026-08-31..09-02
entries (RVMD, CCJ, NU, RUM, NEM, ABNB, MICC, NWS, CXM, PCG), which netted
about -$36 -- mostly RVMD, whose loss the 09-09 outage deepened. It would NOT
have blocked MGM or MICC's 09-25 losses: SPY was above its average that day,
and that selloff was in smaller names. It blocked 14 of 43 sessions from
2026-07-30 to 09-29 while SPY ranged sideways. A small sample; re-judge it.

Runs VM-side, called from scripts/run-context.sh, so the agent gets the verdict
in step 1 at no extra turn or token cost. Talks to Robinhood exactly the way
scripts/news-brief.py does and reuses its MCP plumbing rather than copying it.

Output is one line beginning `MARKET:` followed by BUYS-OK or NO-NEW-BUYS and
the numbers. On any failure it prints `MARKET: CHECK FAILED -- <reason>` and
exits non-zero; the run prompt tells the agent what to do then.

Exit codes: 0 verdict printed | 3 credential | 4 transport | 5 tool error
            6 not enough usable data
"""
import datetime as dt
import importlib.util
import json
import os
import sys
from zoneinfo import ZoneInfo

SYMBOL = "SPY"
SMA_DAYS = 20
MAX_DAY_DROP_PCT = -1.0
# Off since 2026-10-01 (owner: trade more). Still computed and reported, so
# state/trades.jsonl records it on every entry for the weekly review.
USE_SMA_RULE = False
LOOKBACK_CALENDAR_DAYS = 45      # ~30 sessions, comfortably over 20
ET = ZoneInfo("America/New_York")

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "news_brief", os.path.join(_here, "news-brief.py"))
nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nb)


def die(code, msg):
    print(f"MARKET: CHECK FAILED -- {msg}")
    sys.exit(code)


nb.die = die                      # route the shared helpers' failures here


def tool_data(result):
    """The `data` envelope of an MCP tool result, or die."""
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


def connect():
    """Open an MCP session. Returns (token, session)."""
    token = nb.access_token()
    _, hdrs = nb.rpc(token, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "market-regime", "version": "1.0"},
    }, 1, None)
    session = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    nb.rpc(token, "notifications/initialized", {}, None, session)
    return token, session


def regime(token, session, rpc_id=10):
    """Compute the verdict. Returns a dict; dies (exits) on failure.

    Shared with scripts/trade-record.py, which stores this beside each entry.
    """
    today = dt.datetime.now(ET).date()
    start = (today - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
    hist, _ = nb.rpc(token, "tools/call", {
        "name": "get_equity_historicals",
        "arguments": {"symbols": [SYMBOL], "interval": "day",
                      "start_time": f"{start}T00:00:00Z"},
    }, rpc_id, session)
    quote, _ = nb.rpc(token, "tools/call", {
        "name": "get_equity_quotes", "arguments": {"symbols": [SYMBOL]},
    }, rpc_id + 1, session)

    try:
        bars = tool_data(hist)["results"][0]["bars"]
        q = tool_data(quote)["results"][0]
        last = float(q["quote"]["last_trade_price"])
        prev = float(q["quote"]["adjusted_previous_close"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        die(6, f"unexpected response shape ({type(exc).__name__})")

    # Completed sessions only. Interpolated bars are gap-fill with no new
    # information (Robinhood synthesises one for a session it has not
    # finalised yet), and today's bar is still forming.
    closes = {}
    for b in bars:
        if b.get("interpolated"):
            continue
        day = b["begins_at"][:10]
        if day < today.isoformat():
            closes[day] = float(b["close_price"])
    # The bars' latest close is not the official settle; the quote's
    # official prior close is. Use it for that day, adding it if missing.
    official = q.get("close") or {}
    if official.get("date") and official.get("price") and not official.get("interpolated"):
        if official["date"] < today.isoformat():
            closes[official["date"]] = float(official["price"])

    series = [closes[d] for d in sorted(closes)][-SMA_DAYS:]
    if len(series) < SMA_DAYS:
        die(6, f"only {len(series)} completed daily closes, need {SMA_DAYS}")
    sma = sum(series) / SMA_DAYS
    day_pct = (last / prev - 1) * 100

    reasons = []
    if USE_SMA_RULE and last < sma:
        reasons.append(f"below its {SMA_DAYS}-day average")
    if day_pct <= MAX_DAY_DROP_PCT:
        reasons.append(f"down {day_pct:.2f}% today")
    return {
        "verdict": "NO-NEW-BUYS" if reasons else "BUYS-OK",
        "reasons": reasons,
        "spy": round(last, 2),
        "sma20": round(sma, 2),
        "vs_sma_pct": round((last / sma - 1) * 100, 2),
        "day_pct": round(day_pct, 2),
        "prev_close": round(prev, 2),
    }


def main():
    r = regime(*connect())
    why = f" -- {SYMBOL} is {' and '.join(r['reasons'])}" if r["reasons"] else ""
    print(f"MARKET: {r['verdict']}{why}. {SYMBOL} {r['spy']:.2f}, "
          f"{SMA_DAYS}d avg {r['sma20']:.2f} ({r['vs_sma_pct']:+.2f}%), "
          f"today {r['day_pct']:+.2f}% vs prior close {r['prev_close']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
