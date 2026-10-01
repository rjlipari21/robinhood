#!/usr/bin/env python3
"""Fetch Robinhood news for one or more tickers VM-side and print a compact brief.

Why this exists: the `get_equity_news` MCP tool returns full article bodies --
measured at ~1,900 tokens for 5 articles on a single ticker. Screening even a
handful of names that way costs more than the rest of a run, and it crowds
Haiku 4.5's 200K context ceiling. Almost none of those tokens carry decision
value: what the agent needs is the headline, the date, the publisher, and
whether any veto keyword appears. This fetches the same data outside the
agent's context and prints ~100-150 tokens per ticker instead of ~1,900 -- a
~92% reduction that makes it affordable to screen several names per run.

Auth reuses the MCP OAuth credential that Claude Code already maintains at
~/.claude/.credentials.json, the same file scripts/refresh-rh-token.py rotates.
The access token is read at runtime and NEVER printed, logged, or included in
output -- .claude/settings.json denies the Read tool access to that file
outright, and nothing here should undo that from the other direction.

Failure is always soft: any auth, transport, or protocol problem exits non-zero
with a one-line reason on stderr and no partial brief. The agent's documented
fallback is to call the `get_equity_news` MCP tool directly for the one or two
names it actually cares about -- more expensive, but never a blocked run. This
script must never be the reason a protective exit goes unchecked.

Usage:  python3 scripts/news-brief.py KO RVMD NEM
        python3 scripts/news-brief.py --limit 8 ABNB

Exit codes: 0 brief printed | 2 bad arguments | 3 credential problem
            4 transport/protocol failure | 5 tool-level error from Robinhood
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

CREDS = os.path.expanduser("~/.claude/.credentials.json")
MCP_URL = "https://agent.robinhood.com/mcp/trading"
SERVER_MATCH = "robinhood-trading"
TOOL = "get_equity_news"

DEFAULT_LIMIT = 6
MAX_TICKERS = 12          # a run screens 1-3 finalists; 12 is a runaway guard
RECENT_DAYS = 3           # "recent" = the sessions that actually move a swing
TIMEOUT = 30

# A ticker and nothing else. This is the whole reason the script is safe to
# allowlist with a wildcard argument: arguments are validated against this
# before anything is done with them, so `Bash(python3 scripts/news-brief.py *)`
# cannot be turned into a general-purpose fetcher or shell. Anything that does
# not match is a hard exit, not a skip -- a silently-dropped ticker would read
# as "no news found", which is the one wrong answer this must never give.
TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")

# Veto keywords from TRADING_PARAMETERS.md "News & catalyst screen". These FLAG,
# they do not decide -- same philosophy as FUND_HINTS in rank-candidates.py. A
# headline matching "investigation" may be about a competitor; the agent reads
# the headline and judges. Sorted by how strongly each implies "do not buy".
#
# NOTE ON THE TRAILING BOUNDARY: these patterns anchor with \b at the START
# only, deliberately. An earlier version also anchored the end, which silently
# broke every stem: `\b(delist)\b` cannot match "delisting" and
# `(resign)\b` cannot match "resigns", so a real CFO departure or delisting
# notice produced FLAGS: none -- indistinguishable from a clean stock.
# scripts/test-news-flags.py covers both directions (26 positive, 12 negative);
# run it after any edit here, and do not re-add a trailing \b.
VETO_PATTERNS = [
    ("MERGER",       r"\b(acquisit|acquire[sd]?|merger|to be acquired|buyout|take[- ]?private|going private|tender offer)"),
    ("DILUTION",     r"\b(public offering|secondary offering|at[- ]the[- ]market|ATM program|convertible note|dilut|priced.{0,20}offering|registered direct)"),
    ("FRAUD",        r"\b(fraud|investigation|subpoena|SEC (probe|charges|inquiry)|DOJ|class action|restat(e|ing|ement)|short[- ]seller report|accounting irregular)"),
    ("MGMT_EXIT",    r"\b(CEO|CFO|COO|auditor|chief executive|chief financial).{0,30}(resign|depart|step[s]? down|terminat|ouster|fired|out as)"),
    ("SOLVENCY",     r"\b(delist|bankrupt|chapter 11|going concern|reverse split|in default|restructur|liquidat)"),
    ("TRIAL_FAIL",   r"\b(failed|misse[sd].{0,15}endpoint|discontinu|halt(ed)? (the )?trial|complete response letter|FDA reject|not approv)"),
    ("GUIDANCE_CUT", r"\b(cuts? (its )?(guidance|outlook|forecast)|lowers? (guidance|outlook)|profit warning|withdraw.{0,15}guidance)"),
]


def die(code: int, msg: str) -> "None":
    print(f"news-brief: {msg}", file=sys.stderr)
    sys.exit(code)


def access_token() -> str:
    """Read the MCP OAuth access token. Returns the token; never logs it."""
    try:
        with open(CREDS) as fh:
            creds = json.load(fh)
    except FileNotFoundError:
        die(3, f"no credential file at {CREDS}")
    except (OSError, json.JSONDecodeError) as exc:
        die(3, f"credential file unreadable ({type(exc).__name__})")

    for key, entry in creds.get("mcpOAuth", {}).items():
        if SERVER_MATCH not in key:
            continue
        tok = entry.get("accessToken")
        if not tok:
            die(3, "robinhood-trading entry has no accessToken")
        exp = entry.get("expiresAt")
        if exp and int(exp) < int(time.time() * 1000):
            # rh-token-refresh@.timer owns rotation; do not refresh from here.
            die(3, "access token expired -- run scripts/refresh-rh-token.py")
        return tok
    die(3, "no robinhood-trading entry in mcpOAuth")


def rpc(token: str, method: str, params: dict, rpc_id, session: str | None):
    """One JSON-RPC call over MCP streamable HTTP. Returns (result, headers)."""
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if rpc_id is not None:
        body["id"] = rpc_id
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Streamable HTTP may answer either way; accept both.
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
        detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
        if exc.code in (401, 403):
            die(3, f"auth rejected (HTTP {exc.code}) -- token may need refresh")
        die(4, f"{method} failed: HTTP {exc.code} {detail}")
    except (urllib.error.URLError, TimeoutError) as exc:
        die(4, f"{method} failed: {type(exc).__name__}")

    if rpc_id is None:            # a notification has no response body
        return None, got

    payload = None
    if raw.lstrip().startswith("{"):
        payload = json.loads(raw)
    else:
        # SSE: one or more "data: {...}" lines. Take the frame carrying our id.
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
        die(4, f"{method}: no JSON-RPC frame in response")
    if "error" in payload:
        die(5, f"{method}: {payload['error'].get('message', payload['error'])}")
    return payload.get("result"), got


def fetch(token: str, tickers: list, limit: int) -> dict:
    """MCP handshake once, then one tools/call per ticker."""
    _, hdrs = rpc(token, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "news-brief", "version": "1.0"},
    }, 1, None)
    session = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    rpc(token, "notifications/initialized", {}, None, session)

    out = {}
    for i, sym in enumerate(tickers):
        result, _ = rpc(token, "tools/call", {
            "name": TOOL,
            "arguments": {"symbol": sym, "limit": limit},
        }, 10 + i, session)
        out[sym] = parse(result, sym)
    return out


def parse(result, sym: str) -> dict:
    """Pull articles out of an MCP tool result, tolerating shape drift."""
    if not isinstance(result, dict):
        return {"error": "unexpected result type"}
    blocks = result.get("content") or []
    data = None
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            try:
                data = json.loads(b.get("text") or "")
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        data = result.get("structuredContent") or {}
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return {"error": "no data envelope in tool result"}
    arts = payload.get("articles")
    if not isinstance(arts, list):
        return {"error": "no articles array"}
    return {"articles": arts}


def flags_for(text: str) -> list:
    hits = []
    for label, pat in VETO_PATTERNS:
        if re.search(pat, text, re.I):
            hits.append(label)
    return hits


def brief(sym: str, info: dict) -> list:
    """Render one ticker as compact lines. Headlines only -- no article bodies."""
    lines = [f"=== {sym} ==="]
    if "error" in info:
        lines.append(f"  UNAVAILABLE: {info['error']}")
        return lines
    arts = info["articles"]
    if not arts:
        lines.append("  no articles returned")
        return lines

    cutoff = time.time() - RECENT_DAYS * 86400
    all_flags, recent = set(), 0
    for a in arts:
        title = (a.get("title") or "").strip()
        pub = (a.get("publisher") or a.get("source_type") or "?").strip()
        when = (a.get("published_at") or "")[:10]
        # Flag on title + preview only. Bodies are where incidental mentions
        # live (a KO story that is really about Nvidia), so matching them
        # produces flags the agent then has to talk itself out of.
        hay = f"{title} {a.get('preview_text') or ''}"
        f = flags_for(hay)
        all_flags.update(f)
        try:
            ts = time.mktime(time.strptime(when, "%Y-%m-%d"))
            fresh = ts >= cutoff
        except (ValueError, OverflowError):
            fresh = False
        if fresh:
            recent += 1
        mark = "*" if fresh else " "
        tag = (" [" + ",".join(f) + "]") if f else ""
        lines.append(f" {mark}{when} {pub}: {title[:110]}{tag}")

    lines.append(
        f"  {len(arts)} articles, {recent} within {RECENT_DAYS}d (* = recent)"
    )
    lines.append(
        "  FLAGS: " + (",".join(sorted(all_flags)) if all_flags
                       else "none -- no veto keyword in any headline")
    )
    return lines


def main() -> int:
    argv = sys.argv[1:]
    limit = DEFAULT_LIMIT
    if "--limit" in argv:
        i = argv.index("--limit")
        try:
            limit = max(1, min(50, int(argv[i + 1])))
        except (IndexError, ValueError):
            die(2, "--limit needs an integer 1-50")
        del argv[i:i + 2]

    tickers = [a.strip().upper() for a in argv if a.strip()]
    if not tickers:
        die(2, "usage: news-brief.py [--limit N] TICKER [TICKER ...]")
    if len(tickers) > MAX_TICKERS:
        die(2, f"{len(tickers)} tickers requested, max {MAX_TICKERS} "
               "-- this is a finalist gate, not a list screen")
    for t in tickers:
        if not TICKER_RE.match(t):
            die(2, f"{t!r} is not a ticker symbol")
    seen, uniq = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    data = fetch(access_token(), uniq, limit)

    print(f"NEWS BRIEF  {time.strftime('%Y-%m-%d %H:%M %Z')}  "
          f"({len(uniq)} ticker(s), limit {limit}/ticker)")
    print("Headlines only. FLAGS are keyword hits to judge, not verdicts --")
    print("read the headline; a flag may be about a competitor or be stale.")
    print()
    for sym in uniq:
        for line in brief(sym, data[sym]):
            print(line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
