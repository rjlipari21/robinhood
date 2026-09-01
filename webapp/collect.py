#!/usr/bin/env python3
"""Read-only data layer for the trading dashboard.

Gathers three things and never writes anything:

  * live account state from Robinhood over MCP (positions, portfolio, quotes)
  * the agent's decision narrative from state/journal.md + state/archive/
  * executed orders and fills from state/ledger.json and state/fills.jsonl

WHY THE MCP CLIENT IS DUPLICATED HERE. scripts/news-brief.py has a working
client for the same endpoint and this repeats perhaps forty lines of it. That
is deliberate. news-brief.py sits in the trading path -- the agent shells out
to it before placing a buy -- and a refactor that broke it would break trading
to save duplication in a dashboard. The dashboard is strictly downstream of
the money, so it carries its own copy and news-brief.py is never touched.

READ-ONLY IS STRUCTURAL, NOT A CONVENTION. Only the five read tools in
ALLOWED_TOOLS can be called; call() rejects anything else before it builds a
request, so no code path in this process can place, cancel, or modify an order
even if the HTTP layer above it is compromised. The OAuth token is read at
call time and is never returned, logged, cached to disk, or rendered.

Every fetch degrades rather than raising: a failed MCP call returns an error
string that the UI renders as a banner, so a Robinhood outage or an expired
token shows a stale-data warning instead of a blank page.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS = os.path.expanduser("~/.claude/.credentials.json")
MCP_URL = "https://agent.robinhood.com/mcp/trading"
SERVER_MATCH = "robinhood-trading"
ACCOUNT = "797887684"
TIMEOUT = 25

# The whole tool surface this process may touch. Anything absent is
# unreachable -- see the module docstring.
ALLOWED_TOOLS = frozenset({
    "get_equity_positions",
    "get_portfolio",
    "get_accounts",
    "get_equity_quotes",
    "get_equity_orders",
})

CACHE_TTL = 60          # seconds; a browser refresh must not hammer Robinhood
_cache: dict = {}

ET = timezone(timedelta(hours=-4))      # America/New_York, EDT


# --------------------------------------------------------------------------
# MCP transport
# --------------------------------------------------------------------------

class McpError(Exception):
    pass


def _token() -> str:
    """Read the OAuth access token. Never logged, never returned upward."""
    try:
        with open(CREDS) as fh:
            creds = json.load(fh)
    except FileNotFoundError:
        raise McpError("no credential file -- is Claude Code installed?")
    except (OSError, json.JSONDecodeError):
        raise McpError("credential file unreadable")

    for key, entry in creds.get("mcpOAuth", {}).items():
        if SERVER_MATCH not in key:
            continue
        tok = entry.get("accessToken")
        if not tok:
            raise McpError("no accessToken in credential entry")
        exp = entry.get("expiresAt")
        if exp and int(exp) < int(time.time() * 1000):
            raise McpError("access token expired -- rh-token-refresh timer "
                           "should renew it within the hour")
        return tok
    raise McpError("no robinhood-trading entry in credentials")


def _rpc(token, method, params, rpc_id, session):
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if rpc_id is not None:
        body["id"] = rpc_id
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(), method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            got = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise McpError(f"auth rejected (HTTP {exc.code})")
        raise McpError(f"{method}: HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise McpError(f"{method}: {type(exc).__name__}")

    if rpc_id is None:
        return None, got

    payload = None
    if raw.lstrip().startswith("{"):
        payload = json.loads(raw)
    else:                                   # SSE frames
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                frame = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if frame.get("id") == rpc_id or payload is None:
                payload = frame
    if payload is None:
        raise McpError(f"{method}: no JSON-RPC frame in response")
    if "error" in payload:
        raise McpError(f"{method}: {payload['error'].get('message', 'error')}")
    return payload.get("result"), got


def _unwrap(result):
    """Pull the data envelope out of an MCP tool result."""
    if not isinstance(result, dict):
        return None
    for b in result.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text":
            try:
                return (json.loads(b.get("text") or "")).get("data")
            except (json.JSONDecodeError, AttributeError):
                continue
    sc = result.get("structuredContent")
    return sc.get("data") if isinstance(sc, dict) else None


def call(calls: list) -> dict:
    """Run several read-only tool calls over one MCP session.

    `calls` is [(key, tool_name, arguments), ...]; returns {key: data}.
    Raises McpError, which every caller converts into a UI banner.
    """
    for _, tool, _a in calls:
        if tool not in ALLOWED_TOOLS:
            raise McpError(f"tool {tool!r} is not read-only -- refusing")

    token = _token()
    _, hdrs = _rpc(token, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "trading-dashboard", "version": "1.0"},
    }, 1, None)
    session = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    _rpc(token, "notifications/initialized", {}, None, session)

    out = {}
    for i, (key, tool, args) in enumerate(calls):
        result, _ = _rpc(token, "tools/call",
                         {"name": tool, "arguments": args}, 10 + i, session)
        out[key] = _unwrap(result)
    return out


# --------------------------------------------------------------------------
# Live account state
# --------------------------------------------------------------------------

def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _quote_price(quote: dict):
    """Current price from a quote row, or None if it carries no usable price.

    Robinhood returns a regular-session trade and a non-regular (extended /
    off-exchange) trade with separate timestamps, and the tool guide is
    explicit that neither is authoritative on its own: take whichever traded
    most recently. After the close that is normally the non-reg print, which
    is exactly the number a dashboard should show in the evening -- taking
    last_trade_price unconditionally would freeze the page at the 16:00 price
    all night.
    """
    best, best_at = None, ""
    for price_key, time_key in (
        ("last_trade_price", "venue_last_trade_time"),
        ("last_non_reg_trade_price", "venue_last_non_reg_trade_time"),
    ):
        price = _f(quote.get(price_key), 0.0)
        if price <= 0:
            continue
        at = quote.get(time_key) or ""
        if best is None or at > best_at:    # ISO-8601 UTC sorts lexically
            best, best_at = price, at
    return best


def live_state() -> dict:
    """Positions + portfolio + open orders, priced. Cached for CACHE_TTL."""
    hit = _cache.get("live")
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    try:
        first = call([
            ("pos", "get_equity_positions", {"account_number": ACCOUNT}),
            ("pf", "get_portfolio", {"account_number": ACCOUNT}),
            ("acct", "get_accounts", {}),
            ("orders", "get_equity_orders", {"account_number": ACCOUNT}),
        ])
    except McpError as exc:
        stale = _cache.get("live")
        out = (stale[1].copy() if stale else
               {"positions": [], "portfolio": {}, "open_orders": []})
        out["error"] = str(exc)
        out["stale"] = bool(stale)
        return out

    raw_pos = (first.get("pos") or {}).get("positions") or []
    symbols = [p.get("symbol") for p in raw_pos if p.get("symbol")]

    quotes = {}
    quote_err = None
    if symbols:
        try:
            # `symbols` is an array, not a comma-joined string, and the payload
            # is results[].quote paired with results[].close.
            q = call([("q", "get_equity_quotes", {"symbols": symbols})])
            for row in (q.get("q") or {}).get("results") or []:
                quote = (row or {}).get("quote") or {}
                if quote.get("symbol"):
                    quotes[quote["symbol"]] = quote
        except McpError as exc:
            quote_err = str(exc)

    positions = []
    for p in raw_pos:
        sym = p.get("symbol")
        qty = _f(p.get("quantity"))
        entry = _f(p.get("average_buy_price"))
        quote = quotes.get(sym) or {}

        last = _quote_price(quote)
        priced = last is not None
        if not priced:
            last = entry                    # keeps the row renderable at 0%

        prev = _f(quote.get("adjusted_previous_close")) or None
        basis = qty * entry
        value = qty * last
        positions.append({
            "symbol": sym,
            "quantity": qty,
            "entry": entry,
            "last": last,
            "basis": round(basis, 2),
            "value": round(value, 2),
            "pl": round(value - basis, 2),
            "pl_pct": round(((last / entry) - 1) * 100, 2) if entry else 0.0,
            # Day change is against the official prior close, per the tool guide.
            "day_pct": (round(((last / prev) - 1) * 100, 2) if prev else None),
            "bid": _f(quote.get("bid_price")) or None,
            "ask": _f(quote.get("ask_price")) or None,
            "priced": priced,
            # Surfaced rather than swallowed: a halted or never-traded name
            # must not look like a normal quote.
            "tradeable": quote.get("state") == "active" and quote.get("has_traded", True),
            # The two thresholds the agent acts on, so the UI can show distance
            # to each without re-deriving the mandate's numbers.
            "to_stop_pct": round(-5.0 - (((last / entry) - 1) * 100), 2) if entry else None,
            "to_target_pct": round(3.0 - (((last / entry) - 1) * 100), 2) if entry else None,
        })
    positions.sort(key=lambda r: r["pl_pct"])

    pf = first.get("pf") or {}
    accounts = (first.get("acct") or {}).get("accounts") or []
    unsettled = 0.0
    for a in accounts:
        if str(a.get("account_number")) == ACCOUNT:
            unsettled = _f(a.get("unsettled_funds"))
            break

    open_orders = []
    for o in ((first.get("orders") or {}).get("orders") or []):
        if (o.get("state") or "").lower() in ("queued", "confirmed", "partially_filled", "new"):
            open_orders.append({
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "quantity": _f(o.get("quantity")),
                "price": _f(o.get("price")),
                "state": o.get("state"),
                "created_at": o.get("created_at"),
            })

    total = _f(pf.get("total_value"))
    out = {
        "positions": positions,
        "portfolio": {
            "total_value": total,
            "equity_value": _f(pf.get("equity_value")),
            "cash": _f(pf.get("cash")),
            "buying_power": _f((pf.get("buying_power") or {}).get("buying_power")),
            "unsettled_funds": unsettled,
            # The two account-level rules the agent must respect, surfaced so
            # the dashboard shows the same limits the agent is trading under.
            "circuit_breaker": total < 850 if total else False,
            "reserve_ok": (_f(pf.get("cash")) >= total * 0.10) if total else True,
            "slots_used": len(positions),
            "slots_max": 9,
        },
        "open_orders": open_orders,
        "error": None,
        "stale": False,
    }
    _cache["live"] = (time.time(), out)
    return out


# --------------------------------------------------------------------------
# Journal -- the agent's decision narrative
# --------------------------------------------------------------------------

# Headings drifted across formats; all of them carry an ISO date somewhere.
#   ## 2026-09-01 15:30 ET — no action, ...
#   ## 2026-09-01 ~10:50 ET run (first run of the day) — ...
#   ## Run 43 — 2026-08-26, ~17:18 UTC (1:18 PM ET)
_H_DATE = re.compile(r"(20\d\d-\d\d-\d\d)")
_H_TIME = re.compile(r"~?(\d{1,2}):(\d{2})\s*(ET|UTC)?", re.I)
_ACCT_VAL = re.compile(r"account value[^$\d]{0,12}\$?([\d,]+\.\d{2})", re.I)


def _journal_files() -> list:
    files = [os.path.join(REPO, "state", "journal.md")]
    adir = os.path.join(REPO, "state", "archive")
    if os.path.isdir(adir):
        files += sorted(os.path.join(adir, f)
                        for f in os.listdir(adir) if f.endswith(".md"))
    return [f for f in files if os.path.exists(f)]


def _split_entries(text: str) -> list:
    out, head, buf = [], None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if head is not None:
                out.append((head, "\n".join(buf).strip()))
            head, buf = line[3:].strip(), []
        elif head is not None:
            buf.append(line)
    if head is not None:
        out.append((head, "\n".join(buf).strip()))
    return out


def journal(days: int = 7) -> list:
    """Entries from the last `days` days, newest first, across all journal files."""
    cutoff = (datetime.now(ET) - timedelta(days=days)).date()
    seen, entries = set(), []

    for path in _journal_files():
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for head, body in _split_entries(text):
            m = _H_DATE.search(head)
            if not m:
                continue
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if d < cutoff:
                continue
            key = (head, len(body))
            if key in seen:
                continue
            seen.add(key)

            tm = _H_TIME.search(head[m.end():])
            hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (0, 0)
            hh, mm = min(hh, 23), min(mm, 59)

            title = head
            for sep in ("—", " - ", "--"):
                if sep in head:
                    title = head.split(sep, 1)[1].strip()
                    break

            av = _ACCT_VAL.search(body)
            entries.append({
                "date": m.group(1),
                "time": f"{hh:02d}:{mm:02d}",
                "sort": f"{m.group(1)} {hh:02d}:{mm:02d}",
                "heading": head,
                "title": title,
                "body": body,
                "account_value": (float(av.group(1).replace(",", ""))
                                  if av else None),
                "orders_placed": _counts_orders(body),
            })

    entries.sort(key=lambda e: e["sort"], reverse=True)
    return entries


def _counts_orders(body: str) -> int:
    """Best-effort count of orders an entry says it placed.

    Narrative text, so this is a display hint only -- the ledger is the record.
    """
    m = re.search(r"[Oo]rders placed[^:]{0,20}:\s*\*{0,2}(\d+)", body)
    if m:
        return int(m.group(1))
    if re.search(r"\bno orders? (were )?placed|placed (no|zero) orders?\b",
                 body, re.I):
        return 0
    return len(re.findall(r"\bref_id\b", body))


def equity_curve(days: int = 7) -> list:
    """Account value over time, scraped from journal entries.

    Best-effort: entries that do not state a value are skipped. Returned
    oldest-first for plotting.
    """
    pts = [{"t": e["sort"], "v": e["account_value"]}
           for e in journal(days) if e["account_value"]]
    pts.reverse()
    return pts


# --------------------------------------------------------------------------
# Executed orders and fills
# --------------------------------------------------------------------------

def fills(days: int = 7) -> list:
    """Filled orders with the agent's own reasoning note, newest first."""
    path = os.path.join(REPO, "state", "fills.jsonl")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = r.get("filled_at") or ""
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when < cutoff:
                    continue
                out.append({
                    "order_id": r.get("order_id"),
                    "symbol": r.get("symbol"),
                    "side": r.get("side"),
                    "quantity": _f(r.get("quantity")),
                    "price": _f(r.get("average_price")),
                    "filled_at": ts,
                    "note": r.get("note") or "",
                })
    except OSError:
        return []
    out.sort(key=lambda r: r["filled_at"], reverse=True)
    return out


def orders(days: int = 7) -> list:
    """Orders the agent placed, from the hook-maintained ledger."""
    path = os.path.join(REPO, "state", "ledger.json")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            rows = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        ts = r.get("timestamp_utc") or ""
        try:
            when = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            continue
        out.append({
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "quantity": _f(r.get("quantity")),
            "limit_price": _f(r.get("limit_price")),
            "notional": _f(r.get("notional_usd")),
            "at": ts,
            "ref_id": r.get("ref_id"),
        })
    out.sort(key=lambda r: r["at"], reverse=True)
    return out


# --------------------------------------------------------------------------
# Agent health
# --------------------------------------------------------------------------

def agent_status() -> dict:
    """Kill switch, last run, and next scheduled run."""
    halt = os.path.exists(os.path.join(REPO, "state", "HALT"))

    last_run, last_rc = None, None
    logdir = os.path.join(REPO, "logs")
    try:
        logs = sorted(f for f in os.listdir(logdir) if f.startswith("run-"))
    except OSError:
        logs = []
    if logs:
        try:
            with open(os.path.join(logdir, logs[-1]),
                      encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-4000:]
            fin = re.findall(r"run finished (\S+) rc=(\d+)", tail)
            if fin:
                last_run, last_rc = fin[-1][0], int(fin[-1][1])
        except OSError:
            pass

    return {
        "halt": halt,
        "last_run": last_run,
        "last_rc": last_rc,
        "cadence": "hourly on the half hour, 09:30-15:30 ET, Mon-Fri",
        "now_et": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
    }


def snapshot(days: int = 7) -> dict:
    """Everything the dashboard renders, in one payload."""
    live = live_state()
    return {
        "generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
        "window_days": days,
        "status": agent_status(),
        "portfolio": live["portfolio"],
        "positions": live["positions"],
        "open_orders": live["open_orders"],
        "live_error": live.get("error"),
        "live_stale": live.get("stale", False),
        "journal": journal(days),
        "fills": fills(days),
        "orders": orders(days),
        "equity_curve": equity_curve(days),
    }


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, default=str)[:4000])
