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


def _zone(name: str, fallback_hours: int):
    """A named timezone when tzdata is available, else a fixed offset.

    The fallback is only right during DST -- which is what the fixed-offset
    constant this replaced was, unconditionally, all year.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                       # no tzdata on the host
        return timezone(timedelta(hours=fallback_hours))


# EVERY TIME THIS MODULE EMITS IS PACIFIC, because that is the clock the owner
# reads the page on. Eastern still has to exist here: the market, the mandate,
# the scheduler and the journal headings are all stated in it, so ET is what
# journal times are *parsed* as before being converted. Nothing in ET leaves
# this file except as an explicit "as written" string beside the converted
# time -- a page that mixed the two silently would be worse than either.
PT = _zone("America/Los_Angeles", -7)
ET = _zone("America/New_York", -4)


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
    cutoff = (datetime.now(PT) - timedelta(days=days)).date()
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

            # Headings carry the market's clock -- ET, or UTC on a handful of
            # the oldest entries, which is why the label is read rather than
            # assumed. Convert to Pacific for display but keep the string as
            # written: an entry that says "15:30 ET" must not look as though
            # it had been edited to say 12:30.
            tm = _H_TIME.search(head[m.end():])
            e_date, e_time, as_written = m.group(1), "", ""
            if tm:
                hh = min(int(tm.group(1)), 23)
                mm = min(int(tm.group(2)), 59)
                zname = (tm.group(3) or "ET").upper()
                as_written = f"{hh:02d}:{mm:02d} {zname}"
                stated = datetime(d.year, d.month, d.day, hh, mm,
                                  tzinfo=(timezone.utc if zname == "UTC" else ET))
                local = stated.astimezone(PT)
                e_date = local.strftime("%Y-%m-%d")
                e_time = local.strftime("%H:%M")

            title = head
            for sep in ("—", " - ", "--"):
                if sep in head:
                    title = head.split(sep, 1)[1].strip()
                    break

            av = _ACCT_VAL.search(body)
            entries.append({
                # Pacific. An entry whose heading states no time at all keeps
                # its written date and sorts at the top of that day, since
                # there is nothing to convert.
                "date": e_date,
                "time": e_time,
                "time_as_written": as_written,
                "sort": f"{e_date} {e_time or '00:00'}",
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


def equity_curve(days: int = 7, entries=None) -> list:
    """Account value over time, scraped from journal entries.

    Best-effort: entries that do not state a value are skipped. Returned
    oldest-first for plotting.
    """
    entries = journal(days) if entries is None else entries
    pts = [{"t": e["sort"], "v": e["account_value"]}
           for e in entries if e["account_value"]]
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
                    # Present on sells only: the agent records the realized
                    # return against its own entry when it closes a position.
                    "pnl_pct": (None if r.get("pnl_pct") is None
                                else _f(r.get("pnl_pct"), None)),
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


def _utc(ts: str):
    """Parse either record's timestamp shape, or None."""
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def activity(days: int = 7) -> dict:
    """Orders placed joined to the fills they produced, newest first.

    THE JOIN IS INFERRED, and the wording on the page reflects that. The two
    records share no key: the ledger is written by the hook and keys on the
    client-side `ref_id` the agent generated, while fills.jsonl keys on the
    order id Robinhood assigned. So an order is matched to the earliest
    unclaimed fill with the same symbol, side and quantity at a plausible
    time. That is unambiguous in practice because the mandate allows one
    position per ticker and the agent places at most one order per ticker per
    run; where it would not be, the consequence is that two same-shape orders
    swap notes, not that a number is wrong -- every displayed field comes from
    whichever record actually holds it, never from the join.

    The tolerance runs from 3 minutes BEFORE the order to 24 hours after. The
    lower bound is not defensive padding: the hook appends to the ledger after
    the order round-trips, so an order that fills instantly is recorded in
    fills.jsonl with a timestamp fractionally earlier than its own ledger row.

    An unmatched order is reported `unfilled`, which for the current session
    can mean "still working" rather than "expired" -- `live_state()`'s
    open_orders is the authority on that, and the page says so. An unmatched
    fill means the order that produced it was placed before this window, so it
    is shown with no limit price rather than dropped: it is still a trade that
    happened.
    """
    placed = orders(days)
    got = fills(days)
    for f in got:
        f["_claimed"] = False

    events, matched = [], 0
    for o in sorted(placed, key=lambda r: r["at"]):
        o_at = _utc(o["at"])
        best = None
        for f in sorted(got, key=lambda r: r["filled_at"]):
            if f["_claimed"] or f["symbol"] != o["symbol"] or f["side"] != o["side"]:
                continue
            if abs(f["quantity"] - o["quantity"]) > 1e-6:
                continue
            f_at = _utc(f["filled_at"])
            if o_at is None or f_at is None:
                continue
            if -180 <= (f_at - o_at).total_seconds() <= 86400:
                best = f
                break
        if best is not None:
            best["_claimed"] = True
            matched += 1
        events.append(_event(o, best))

    for f in got:
        if not f["_claimed"]:
            events.append(_event(None, f))

    events.sort(key=lambda e: e["at"], reverse=True)
    for f in got:
        f.pop("_claimed", None)

    closed = [e["pnl_pct"] for e in events
              if e["side"] == "sell" and e["pnl_pct"] is not None]
    wins = [v for v in closed if v > 0]
    return {
        "events": events,
        "placed": len(placed),
        "matched": matched,
        "unfilled": len(placed) - matched,
        "fill_rate": round(100.0 * matched / len(placed), 1) if placed else None,
        # Fills whose order predates the window. Counted separately so the
        # fill rate above stays a like-for-like ratio of this window's orders.
        "earlier_fills": sum(1 for e in events if e["kind"] == "fill" and not e["ref_id"]),
        "closed": len(closed),
        "wins": len(wins),
        "losses": sum(1 for v in closed if v < 0),
        "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else None,
        "avg_pnl_pct": round(sum(closed) / len(closed), 2) if closed else None,
        "best_pnl_pct": max(closed) if closed else None,
        "worst_pnl_pct": min(closed) if closed else None,
        "hit_target": sum(1 for v in closed if v >= 3.0),
        "hit_stop": sum(1 for v in closed if v <= -5.0),
    }


def _event(order, fill) -> dict:
    """One activity row. Every field is sourced from the record that holds it."""
    src = order or fill
    limit = order["limit_price"] if order else None
    price = fill["price"] if fill else None
    # Signed so that positive always means "better than the limit asked for",
    # which is the opposite arithmetic on a buy and on a sell.
    vs_limit = None
    if limit and price:
        vs_limit = round(100.0 * (limit - price) / limit, 3)
        if src["side"] == "sell":
            vs_limit = -vs_limit
    at = fill["filled_at"] if fill else order["at"]
    # Formatted here rather than in the browser: the page states Pacific
    # throughout, and the client has no business deriving that from whatever
    # clock the viewer's machine happens to be set to. Robinhood returns UTC,
    # so this is the only conversion an order timestamp gets.
    local = _utc(at)
    local = local.astimezone(PT) if local else None
    return {
        "kind": "fill" if fill else "unfilled",
        "symbol": src["symbol"],
        "side": src["side"],
        "quantity": src["quantity"],
        "limit_price": limit,
        "price": price,
        "notional": order["notional"] if order else (
            round((price or 0) * src["quantity"], 2)),
        "at": at,
        "date_pt": local.strftime("%Y-%m-%d") if local else "",
        "time_pt": local.strftime("%H:%M") if local else "",
        "placed_at": order["at"] if order else None,
        "vs_limit_pct": vs_limit,
        "pnl_pct": (fill or {}).get("pnl_pct"),
        "note": (fill or {}).get("note") or "",
        "ref_id": order["ref_id"] if order else None,
        "order_id": fill["order_id"] if fill else None,
    }


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

    # The log records UTC; the page shows Pacific like everything else.
    stamp = _utc(last_run) if last_run else None
    return {
        "halt": halt,
        "last_run": (stamp.astimezone(PT).strftime("%Y-%m-%d %H:%M") if stamp
                     else last_run),
        "last_rc": last_rc,
        # The scheduler is set in ET because the market is; 06:30-12:30 is the
        # same seven runs read off a Pacific clock.
        "cadence": "hourly on the half hour, 06:30-12:30 PT, Mon-Fri",
        "cadence_et": "09:30-15:30 ET",
        "now_pt": datetime.now(PT).strftime("%Y-%m-%d %H:%M PT"),
    }


# --------------------------------------------------------------------------
# Static guidelines -- the rules every run trades under
# --------------------------------------------------------------------------

# WHY THE NUMBERS ARE READ, NOT RESTATED. config/limits.json is the file
# hooks/guardrails.py actually enforces, so the caps below are pulled from it
# at request time. A hard-coded copy here would keep rendering $500 the day the
# cap moved, and a dashboard that misstates the limits the agent trades under
# is worse than one that never showed them.
LIMITS_DEFAULT = {
    "max_position_usd": 500.0, "max_orders_per_day": 50, "max_positions": 9,
    "min_price_usd": 5.0, "min_cash_reserve_pct": 10.0,
    "circuit_breaker_value_usd": 850.0, "allowed_types": ["limit"],
    "allowed_market_hours": ["regular_hours"], "account_number": ACCOUNT,
}


def _limits() -> dict:
    out = dict(LIMITS_DEFAULT)
    try:
        with open(os.path.join(REPO, "config", "limits.json"),
                  encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict):
            out.update({k: v for k, v in got.items() if v is not None})
    except (OSError, json.JSONDecodeError):
        out["_source"] = "defaults — config/limits.json unreadable"
    return out


def guidelines() -> dict:
    """The mandate's investing rules, grouped, with each rule's enforcement layer.

    Static on purpose. Everything else on this page is behaviour -- what the
    agent did -- and behaviour is only judgeable against the rules it was
    supposed to follow, which are otherwise only visible by reading CLAUDE.md
    on the VM. `layer` is the distinction that matters most: "hook" rules are
    rejected in code before an order reaches Robinhood, "agent" rules the hook
    cannot see and the agent must enforce against live account state each run.
    A reader auditing a bad trade needs to know which kind was broken.

    This is a mirror of CLAUDE.md and has to be edited when the mandate is.
    Only the caps read themselves, from the file the hook enforces; the numbers
    quoted in the prose here and in SOURCE_CATALOG are checked against that
    same file by scripts/test-dashboard.sh, so a changed limit fails the test
    rather than quietly misinforming the owner.
    """
    L = _limits()
    money = lambda v: f"${float(v):,.0f}"
    groups = [
        ("Account and instrument", [
            ("hook", f"Trade only the agentic cash account {L['account_number']}."),
            ("hook", "Limit orders only, entries and exits alike — market and "
                     "stop orders are rejected."),
            ("hook", "Regular-hours orders only. Every run happens inside "
                     "06:30-12:30 PT, so an order can never be left working "
                     "while nothing is awake to manage it."),
            ("agent", "US-listed common stocks only. No ETFs, funds, trusts, "
                      "options, crypto, margin or leveraged products."),
            ("hook", f"Price floor {money(L['min_price_usd'])} per share."),
        ]),
        ("Position sizing", [
            ("hook", f"At most {money(L['max_position_usd'])} in any one name — "
                     "a ceiling for the highest-conviction setups, not a target."),
            ("agent", f"At most {L['max_positions']} concurrent positions, one "
                      "per ticker."),
            ("agent", f"Keep at least {L['min_cash_reserve_pct']:.0f}% of account "
                      "value in cash at all times."),
            ("agent", "Buy with settled funds only. Sale proceeds are unspendable "
                      "until T+1 settlement, which is what avoids good-faith "
                      "violations."),
            ("hook", f"At most {L['max_orders_per_day']} buy orders per day."),
        ]),
        ("Entry — buy the trending low", [
            ("agent", "No watchlist. Candidates come from two saved scanners "
                      "every run, ranked by relative volume, capped at 50 names "
                      "before any per-name analysis."),
            ("agent", "Buy an uptrending or basing name that has pulled back to "
                      "support: hourly RSI at or below 35, or price near the low "
                      "of its 5-10 day range, or a 2%+ dip with the higher "
                      "timeframe still up."),
            ("agent", "Confirm on historicals and technical indicators, never on "
                      "a quote alone — successive lower closes are a name still "
                      "falling, not a base."),
            ("agent", "News screen before every buy. Veto on a pending buyout, a "
                      "dilutive offering, fraud or an investigation, delisting or "
                      "going-concern risk, or a failed trial."),
            ("agent", "Veto any name reporting earnings inside the next 2 trading "
                      "days."),
            ("agent", "Check book depth and average volume: a limit order in a "
                      "thin name sits unfilled or fills badly."),
            ("agent", "When a setup is marginal, skip it. Most runs should place "
                      "no orders at all."),
        ]),
        ("Exit — sell the trending high", [
            ("agent", "Protective exit: close any position down 5% or more from "
                      "entry with a limit sell. There are no stop orders, so this "
                      "only happens because a run checked — it is done first, "
                      "every run, before looking for entries."),
            ("agent", "Profit target: +3-5% from entry, or hourly RSI at or above "
                      "65, or price at the top of its recent range."),
            ("agent", "Never carry a position through its own earnings print. "
                      "Close it on the 11:30 or 12:30 PT run when it reports "
                      "after today's close or any time the next trading day."),
            ("agent", "Check the news behind a triggered position before selling. "
                      "It does not make the exit discretionary — it records "
                      "whether the move was company-specific or market noise."),
            ("agent", "Never average down into a position more than once."),
        ]),
        ("Circuit breakers", [
            ("agent", f"Below {money(L['circuit_breaker_value_usd'])} of account "
                      "value, open no new positions. Keep managing exits and wait "
                      "for owner instructions."),
            ("agent", "On any order rejection, unexpected balance or tool failure, "
                      "stop trading for the run and record what happened."),
            ("hook", "The state/HALT kill switch ends a run before it can place "
                     "anything."),
        ]),
        ("Cadence and record", [
            ("agent", "Seven runs a day, hourly on the half hour, 06:30-12:30 PT, "
                      "Monday to Friday. Nothing runs overnight or at weekends."),
            ("agent", "Each run sees roughly every twelfth 5-minute bar, so a "
                      "threshold may have been crossed and recovered in bars no "
                      "run ever saw."),
            ("agent", "Nothing manages a position between 13:00 and 06:30 PT — "
                      "17.5 hours. The first run of the day is the one most "
                      "likely to need action."),
            ("agent", "Append a journal entry every run, orders or not, and record "
                      "rejections and losing trades plainly."),
        ]),
    ]
    return {
        "groups": [{"title": t,
                    "rules": [{"layer": lay, "text": txt} for lay, txt in rs]}
                   for t, rs in groups],
        "limits": L,
        "counts": {
            "hook": sum(1 for _, rs in groups for lay, _t in rs if lay == "hook"),
            "agent": sum(1 for _, rs in groups for lay, _t in rs if lay == "agent"),
        },
    }


# --------------------------------------------------------------------------
# Decision inputs -- which data source fed which order
# --------------------------------------------------------------------------

# The catalogue is the mandate's own pipeline, one row per thing the agent is
# told to look at before it places an order. `sides` is which kind of order the
# source informs; `required` is the sides where the mandate makes it mandatory
# rather than supporting.
#
# `patterns` is how a source is EVIDENCED rather than assumed: the agent names
# its inputs in the journal entry for the run ("hourly RSI 25.0", "ran both
# saved scans", "get_equity_tradability came back untradable"), so an order is
# matched to its run's entry and the entry is searched for each source. That
# makes the attribution auditable -- every citation on the page shows the line
# it came from -- but it also makes it a lower bound: the journal is prose, and
# a source the agent used without writing down reads here as uncited. The UI
# says so rather than presenting absence as proof.
SOURCE_CATALOG = [
    {
        "id": "account", "label": "Account state",
        "origin": "get_equity_positions, get_portfolio, get_accounts",
        "provides": "positions held, slots used, cash, and settled versus "
                    "unsettled funds",
        "feeds": "the 9-position ceiling, the 10% cash reserve and "
                 "settled-funds discipline",
        "sides": ["buy", "sell"], "required": ["buy", "sell"],
        "patterns": r"get_equity_positions|get_portfolio|get_accounts|"
                    r"buying[_ ]power|unsettled|settled cash|cash reserve",
    },
    {
        "id": "scan", "label": "Saved scanners",
        "origin": "run_scan x2 + scripts/rank-candidates.py",
        "provides": "the candidate universe — both scans filter to hourly RSI "
                    "at or below 35, then rows rank by relative volume",
        "feeds": "where candidates come from at all, since there is no watchlist",
        "sides": ["buy"], "required": ["buy"],
        "patterns": r"run_scan|saved scans?|both scans|rank-candidates|"
                    r"scanner|top[- ]50|relvol|relative volume",
    },
    {
        "id": "quote", "label": "Live quotes",
        "origin": "get_equity_quotes",
        "provides": "last trade, bid and ask",
        "feeds": "the $5 price floor, P&L against entry, and where the limit "
                 "price is set",
        "sides": ["buy", "sell"], "required": ["buy", "sell"],
        "patterns": r"get_equity_quotes|quotes?\b|bid[ /x]|ask[ /x]|spread|"
                    r"\bmid\b",
    },
    {
        "id": "historicals", "label": "Price history",
        "origin": "get_equity_historicals",
        "provides": "5-minute and daily bars",
        "feeds": "range position and the higher-timeframe trend — a base "
                 "against a name still falling",
        "sides": ["buy", "sell"], "required": ["buy"],
        "patterns": r"get_equity_historicals|historicals?|"
                    r"5-?min(?:ute)? bars?|hourly bars?|daily bars?|"
                    r"lower closes|\d+[- ]day range|range (?:low|high|position)",
    },
    {
        "id": "technicals", "label": "Technical indicators",
        "origin": "get_equity_technical_indicators",
        "provides": "hourly RSI",
        "feeds": "the RSI-at-or-below-35 entry trigger and the "
                 "RSI-at-or-above-65 profit exit",
        "sides": ["buy", "sell"], "required": ["buy"],
        "patterns": r"get_equity_technical_indicators|technical indicators?|"
                    r"technicals|hourly rsi|rsi ?1h|rsi[ :=]",
    },
    {
        "id": "book", "label": "Order-book depth",
        "origin": "get_equity_price_book",
        "provides": "resting size on each side of the spread",
        "feeds": "whether a limit order of this size can fill cleanly",
        "sides": ["buy"], "required": ["buy"],
        # The last three alternatives are how a book check is usually written
        # down in practice -- "bid $9.72 x 63k / ask $9.73 x 56k", "98k shares
        # available" -- rather than by naming the tool. The size on the x is
        # required to be two digits so that "scans x2" is not read as depth.
        "patterns": r"get_equity_price_book|price[- ]book|order book|\bbook\b|"
                    r"\bdepth\b|thin(?:ly)? (?:book|traded)|"
                    r"[x×] ?\d{2,}[km]?\b|shares? available|\bresting\b",
    },
    {
        "id": "tradability", "label": "Session eligibility",
        "origin": "get_equity_tradability",
        "provides": "which sessions the name may be traded in",
        "feeds": "the session the order is tagged to",
        "sides": ["buy"], "required": ["buy"],
        "patterns": r"get_equity_tradability|tradabilit|tradable|untradable|"
                    r"eligible for (?:regular|extended)",
    },
    {
        "id": "news", "label": "News screen",
        "origin": "scripts/news-brief.py (fallback: get_equity_news)",
        "provides": "recent headlines, keyword-flagged",
        "feeds": "the buy veto on a buyout, an offering, fraud or a failed "
                 "trial — and on a triggered exit, whether the move was "
                 "company-specific or market noise",
        "sides": ["buy", "sell"], "required": ["buy"],
        "patterns": r"news[- ]brief|get_equity_news|"
                    r"news (?:screen|check|brief|feed|flag)|headlines?|FLAGS",
    },
    {
        "id": "earnings", "label": "Earnings date",
        "origin": "get_earnings_results (fallback: get_earnings_calendar)",
        "provides": "the next report date, its am/pm timing, and whether it is "
                    "confirmed",
        "feeds": "the 2-trading-day entry veto and the rule against holding "
                 "through a print",
        "sides": ["buy", "sell"], "required": ["buy", "sell"],
        "patterns": r"get_earnings_results|get_earnings_calendar|"
                    r"earnings (?:date|check|screen|calendar|results|veto|"
                    r"inside|risk)|reports (?:am|pm)|no earnings data|earnings",
    },
    {
        "id": "review", "label": "Pre-trade review",
        "origin": "review_equity_order",
        "provides": "Robinhood's own cost estimate and any order alerts",
        "feeds": "the mandate's requirement to review before placing",
        "sides": ["buy", "sell"], "required": ["buy", "sell"],
        "patterns": r"review_equity_order|reviewed the order|"
                    r"pre-?trade (?:review|estimate)|review(?:ed)? first",
    },
    {
        "id": "journal", "label": "Prior-run journal",
        "origin": "state/journal.md",
        "provides": "entry prices, recorded earnings dates, and names vetoed "
                    "on earlier runs",
        "feeds": "P&L against entry and the arithmetic behind the earnings "
                 "exit, both of which need a number no live call returns",
        "sides": ["buy", "sell"], "required": ["buy", "sell"],
        "patterns": r"state/journal|journal(?:\.md)?\b|last run|previous run|"
                    r"prior run|carried forward|recorded (?:entry|earnings)",
    },
    {
        "id": "guardrails", "label": "Hook guardrails",
        "origin": "hooks/guardrails.py + config/limits.json",
        "provides": "the hard caps, checked on the order itself",
        "feeds": "account, order type, session, position cap and daily count — "
                 "a rejected order never reaches Robinhood",
        "sides": ["buy", "sell"], "required": ["buy", "sell"],
        # Not a journal match: a ledger row exists only because the hook
        # evaluated this order and let it through, so the row IS the evidence.
        "patterns": None, "evidence": "ledger",
    },
]

_SRC_RE = {c["id"]: re.compile(c["patterns"], re.I)
           for c in SOURCE_CATALOG if c.get("patterns")}


def _blocks(body: str) -> list:
    """Body split on blank lines.

    The unit of attribution is a block, not a line: the journal wraps prose, so
    the sentence naming a source is often not the line naming the ticker, but
    both sit in the same paragraph, bullet or table row.
    """
    out, buf = [], []
    for line in body.splitlines():
        if line.strip():
            buf.append(line)
        elif buf:
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def _snippet(text: str, match) -> str:
    """The line a match landed on, windowed so the matched phrase is visible.

    Trimming a long line from its start is what an obvious implementation does
    and it is wrong here: journal lines run past 300 characters, so the phrase
    that evidenced the source is routinely the part that gets cut, leaving a
    quote that does not support the claim beside it. The window is centred on
    the match instead, with an ellipsis on whichever side was cut.
    """
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    end = end if end != -1 else len(text)
    line, at = text[start:end], match.start() - start

    keep = 180
    if len(line) > keep:
        lo = max(0, at - keep // 2)
        hi = min(len(line), lo + keep)
        lo = max(0, hi - keep)
        line = ("…" if lo else "") + line[lo:hi] + ("…" if hi < len(line) else "")
    return re.sub(r"\s+", " ", line).strip(" -*|#>").strip()


def _run_entry(entries):
    """Index run entries by start time so an order can find the run that placed it."""
    starts = []
    for e in entries:
        if not e["time"]:
            continue                        # no time in the heading to place it by
        try:
            starts.append((datetime.strptime(f"{e['date']} {e['time']}",
                                             "%Y-%m-%d %H:%M")
                           .replace(tzinfo=PT), e))
        except ValueError:
            continue
    starts.sort(key=lambda r: r[0])
    return starts


def decisions(days: int = 7, entries=None, acts=None) -> dict:
    """Every order joined to the data sources evidenced behind it.

    The journal entry for a run is the evidence, so an order is first matched
    to its run: entries are stamped with the run's start time and an order
    belongs to the latest run that started no later than it did (plus five
    minutes of slack, since a heading is written to the minute the run began
    and the order lands a moment after). A citation inside a passage that names
    the ticker is `direct`; the same source found elsewhere in the entry is
    `run`, because it was part of the same decision but may have been written
    about another name. `direct` claims the passage and not the line: the unit
    of matching is a paragraph, so the quoted line can be a neighbour of the
    one naming the ticker, and the UI's label says only that.
    """
    entries = journal(days) if entries is None else entries
    acts = activity(days) if acts is None else acts
    starts = _run_entry(entries)

    # Counted per side, because `required` differs per side: the news screen is
    # mandatory on every buy but only expected on a sell that actually
    # triggered, so one blended percentage would misread both.
    rows = []
    tally = {c["id"]: {s: {"applies": 0, "cited": 0, "direct": 0}
                       for s in ("buy", "sell")} for c in SOURCE_CATALOG}

    for ev in acts.get("events", []):
        when = _utc(ev["at"])
        when = when.astimezone(PT) if when else None

        entry, best = None, None
        if when is not None:
            for start, cand in starts:
                if start <= when + timedelta(minutes=5):
                    if best is None or start > best:
                        best, entry = start, cand
                elif entry is not None:
                    break
            # A run three hours away did not place this order.
            if best is not None and (when - best) > timedelta(hours=3):
                entry, best = None, None

        body = entry["body"] if entry else ""
        sym = re.compile(r"\b" + re.escape(ev["symbol"] or "") + r"\b") \
            if ev.get("symbol") else None
        about, other = [], []
        for blk in _blocks(body):
            (about if (sym and sym.search(blk)) else other).append(blk)
        about_text, other_text = "\n\n".join(about), "\n\n".join(other)

        cited, missing, gaps = [], [], []
        for c in SOURCE_CATALOG:
            if ev["side"] not in c["sides"]:
                continue
            t = tally[c["id"]][ev["side"]]
            t["applies"] += 1
            required = ev["side"] in c["required"]

            if c.get("evidence") == "ledger":
                if ev.get("ref_id"):
                    cited.append({"id": c["id"], "tier": "ledger",
                                  "quote": "every order in state/ledger.json "
                                           "passed the hook to get there"})
                    t["cited"] += 1
                    t["direct"] += 1
                    continue
                missing.append(c["id"])
                continue

            rx = _SRC_RE[c["id"]]
            hit = rx.search(ev.get("note") or "")
            if hit:
                cited.append({"id": c["id"], "tier": "direct",
                              "quote": _snippet(ev["note"], hit)})
            elif (hit := rx.search(about_text)):
                cited.append({"id": c["id"], "tier": "direct",
                              "quote": _snippet(about_text, hit)})
            elif (hit := rx.search(other_text)):
                cited.append({"id": c["id"], "tier": "run",
                              "quote": _snippet(other_text, hit)})
            else:
                missing.append(c["id"])
                if required:
                    gaps.append(c["id"])
                continue
            t["cited"] += 1
            if cited[-1]["tier"] == "direct":
                t["direct"] += 1

        rows.append({
            # Whatever identifies this order elsewhere: the ref_id the agent
            # generated, or the id Robinhood assigned when only a fill is on
            # record. Rendered, so a row can be tied back to the ledger. The
            # order's size and price are deliberately not repeated here -- the
            # activity card above owns those, and this card owns the inputs.
            "key": ev.get("ref_id") or ev.get("order_id") or ev["at"],
            "key_kind": ("ref_id" if ev.get("ref_id")
                         else "order_id" if ev.get("order_id") else "timestamp"),
            "symbol": ev["symbol"], "side": ev["side"],
            "date_pt": ev["date_pt"], "time_pt": ev["time_pt"],
            "run": ({"date": entry["date"], "time": entry["time"],
                     "as_written": entry.get("time_as_written", ""),
                     "title": entry["title"]} if entry else None),
            "cited": cited,
            "missing": missing,
            "missing_required": gaps,
        })

    coverage = []
    for c in SOURCE_CATALOG:
        per = tally[c["id"]]
        n = per["buy"]["applies"] + per["sell"]["applies"]
        got = per["buy"]["cited"] + per["sell"]["cited"]
        row = {
            "id": c["id"], "label": c["label"], "origin": c["origin"],
            "provides": c["provides"], "feeds": c["feeds"],
            "sides": c["sides"], "required": c["required"],
            "applies": n, "cited": got,
            "direct": per["buy"]["direct"] + per["sell"]["direct"],
            "pct": round(100.0 * got / n, 1) if n else None,
        }
        for side in ("buy", "sell"):
            row[side] = dict(per[side])
            row[side]["required"] = side in c["required"]
            row[side]["pct"] = (round(100.0 * per[side]["cited"]
                                      / per[side]["applies"], 1)
                                if per[side]["applies"] else None)
        coverage.append(row)

    # No separate catalogue: `coverage` is built from every row of
    # SOURCE_CATALOG whether or not an order exercised it, so it already
    # carries each source's description, and a second copy would be one more
    # thing to keep in step.
    #
    # No aggregate "orders missing a mandatory screen" either, though it is a
    # one-line sum. Two of the mandatory sources -- the pre-trade review and
    # the journal read -- are things the agent does every run and writes down
    # only sometimes, so that count lands near zero-of-everything and reads as
    # an indictment of the trading rather than of the record-keeping. The
    # per-source bars say the same thing without the false headline.
    return {
        "coverage": coverage,
        "orders": rows,
        "buys": sum(1 for r in rows if r["side"] == "buy"),
        "sells": sum(1 for r in rows if r["side"] == "sell"),
        "unmatched_runs": sum(1 for r in rows if r["run"] is None),
    }


def snapshot(days: int = 7) -> dict:
    """Everything the dashboard renders, in one payload."""
    live = live_state()
    entries = journal(days)
    acts = activity(days)
    return {
        "generated_at": datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S PT"),
        "window_days": days,
        "status": agent_status(),
        "portfolio": live["portfolio"],
        "positions": live["positions"],
        "open_orders": live["open_orders"],
        "live_error": live.get("error"),
        "live_stale": live.get("stale", False),
        "journal": entries,
        "fills": fills(days),
        "orders": orders(days),
        "activity": acts,
        "equity_curve": equity_curve(days, entries),
        # Passed the already-parsed journal and activity rather than letting it
        # re-read them: both are the expensive part of a snapshot.
        "decisions": decisions(days, entries, acts),
        "guidelines": guidelines(),
    }


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, default=str)[:4000])
