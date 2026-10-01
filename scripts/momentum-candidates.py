#!/usr/bin/env python3
"""Momentum (breakout) candidates for entry Path B, ranked and compact.

Both saved scans filter to hourly RSI <= 35, so they only ever return dips; a
stock breaking out to new highs is excluded by construction. This supplies
the other half: names in a confirmed uptrend that have just cleared their
recent highs on heavy volume, but are not yet overbought.

Filter set (evaluated live with preview_scan, which saves nothing -- scan
writes are denied in .claude/settings.json and stay that way):
  * common-stock basics, as on the dip scans: asset type STOCK, price > $5,
    market cap > $300M, 30-day average volume > 500K
  * hourly RSI(14) between 50 and 68 -- trending, not yet overbought
  * price above its 50-day average close -- the higher-timeframe trend is up
  * price above the high of the previous 10 daily candles -- a live breakout
    (high() excludes the current, unfinished candle; verified 2026-10-01:
    with `>` it returned 15 names, so today's own high is not included)
  * relative volume >= 1.5, PRO-RATED by how much of the session has passed

Why pro-rated: relative volume is today's volume so far over the 30-day
average full-day volume, so at 09:30 every stock reads near zero and a flat
1.5 bar would return nothing until the afternoon. The threshold is scaled
linearly by the fraction of the 390-minute session elapsed. Intraday volume
is heavier at the open than linear, so this is lenient early in the day --
the agent confirms volume on hourly bars before buying anyway.

Prints at most MAX_ROWS rows, relative volume descending, with the 10-day
breakout level so the agent can see how far price already is past it.
Reuses scripts/news-brief.py's MCP plumbing and rank-candidates.py's fund
flag. Failure is soft: one line, non-zero exit; the agent then skips
momentum entries for the run (dip entries are unaffected).

Exit codes: 0 list printed | 3 credential | 4 transport | 5 tool error
"""
import datetime as dt
import importlib.util
import json
import os
import sys
from zoneinfo import ZoneInfo

MAX_ROWS = 15
RSI_BAND = ("50", "68")
BREAKOUT_DAYS = 10
TREND_DAYS = 50
RELVOL_FULL_DAY = 1.5
ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nb = _load("news_brief", "news-brief.py")
FUND_HINTS = _load("rank_candidates", "rank-candidates.py").FUND_HINTS


def die(code, msg):
    print(f"momentum-candidates: {msg} -- skip momentum entries this run")
    sys.exit(code)


nb.die = die


def session_fraction(now):
    """Fraction of the 09:30-16:00 session elapsed, clamped to [0.05, 1]."""
    open_ = now.replace(hour=9, minute=30, second=0, microsecond=0)
    frac = (now - open_).total_seconds() / (390 * 60)
    return min(1.0, max(0.05, frac))


def filters(relvol_min):
    def expr(e, title=None, pred="PREDICATE_EQUAL", values=("True",)):
        f = {"expression": e, "predicate": pred, "values": list(values)}
        if title:
            f["display_title"] = title
        return f
    return [
        {"filter_type": "FILTER_TYPE_INSTRUMENT_TYPE", "predicate": "PREDICATE_EQUAL", "values": ["STOCK"]},
        {"filter_type": "FILTER_TYPE_LAST", "predicate": "PREDICATE_GREATER_THAN", "values": ["5"]},
        {"filter_type": "FILTER_TYPE_MARKET_CAP", "predicate": "PREDICATE_GREATER_THAN", "values": ["300000000"]},
        {"filter_type": "FILTER_TYPE_AVERAGE_VOLUME", "predicate": "PREDICATE_GREATER_THAN",
         "values": ["500000"], "interval": "1d", "length": 30},
        {"filter_type": "FILTER_TYPE_RSI", "predicate": "PREDICATE_BETWEEN",
         "values": list(RSI_BAND), "interval": "1h", "length": 14},
        expr(f'tradeAllDay.price > closeAvg(candleCount={TREND_DAYS}, candlePeriod="1d", session="all")',
             f"Above {TREND_DAYS}-day avg"),
        expr(f'tradeAllDay.price > high(candleCount={BREAKOUT_DAYS}, candlePeriod="1d", session="all")',
             f"Above {BREAKOUT_DAYS}-day high"),
        expr('dayVolume / volumeAvg(candleCount=30, candlePeriod="1d", session="all")',
             "Relative volume", "PREDICATE_GREATER_THAN_OR_EQUAL", [f"{relvol_min:.3f}"]),
    ]


COLUMNS = [{"display_name": f"{BREAKOUT_DAYS}d high",
            "expression": f'high(candleCount={BREAKOUT_DAYS}, candlePeriod="1d", session="all")'}]


def num(cols, key):
    try:
        return float(cols.get(key))
    except (TypeError, ValueError):
        return None


def main():
    now = dt.datetime.now(ET)
    frac = session_fraction(now)
    relvol_min = RELVOL_FULL_DAY * frac

    token = nb.access_token()
    _, hdrs = nb.rpc(token, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "momentum-candidates", "version": "1.0"},
    }, 1, None)
    session = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    nb.rpc(token, "notifications/initialized", {}, None, session)
    res, _ = nb.rpc(token, "tools/call", {
        "name": "preview_scan",
        "arguments": {"filters": filters(relvol_min), "columns": COLUMNS},
    }, 10, session)
    if (res or {}).get("isError"):
        die(5, "preview_scan returned an error")

    data = None
    for b in (res or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text":
            try:
                data = json.loads(b.get("text") or "").get("data", {}).get("result")
            except (json.JSONDecodeError, AttributeError):
                continue
            break
    if not isinstance(data, dict):
        die(5, "no result in preview_scan response")

    rows = []
    for r in data.get("results") or []:
        c = r.get("columns") or {}
        last, hi = num(c, "Last"), num(c, f"{BREAKOUT_DAYS}d high")
        rows.append({
            "ticker": r.get("ticker"), "last": last, "hi": hi,
            "past": (last / hi - 1) * 100 if last and hi else None,
            "pct": (num(c, "% Change") or 0) * 100,
            "relvol": num(c, "Relative volume") or 0,
            "rsi": num(c, "RSI (14, 1H)"),
            "name": (c.get("Name") or "")[:40],
        })
    rows.sort(key=lambda r: (-r["relvol"], -r["pct"]))
    shown = rows[:MAX_ROWS]

    print(f"# momentum preview {now:%H:%M} ET: {data.get('total_items', len(rows))} match, "
          f"top {len(shown)} shown; relvol >= {relvol_min:.2f} "
          f"({RELVOL_FULL_DAY} x {frac:.0%} of session elapsed)")
    print("# past_brk = % above the prior 10-day high. FUND? = check it is a common stock.")
    print("ticker\tlast\t10d_high\tpast_brk\tpct_chg\trelvol\trsi_1h\tflag\tname")
    for r in shown:
        f = lambda v, p: "-" if v is None else f"{v:{p}}"
        flag = "FUND?" if FUND_HINTS.search(r["name"]) else ""
        print(f"{r['ticker']}\t{f(r['last'], '.2f')}\t{f(r['hi'], '.2f')}\t"
              f"{f(r['past'], '+.2f')}\t{r['pct']:+.2f}\t{r['relvol']:.2f}\t"
              f"{f(r['rsi'], '.1f')}\t{flag}\t{r['name']}")
    if not shown:
        print("(no momentum candidates this run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
