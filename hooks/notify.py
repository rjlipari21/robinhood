#!/usr/bin/env python3
"""Push notifications for order activity.

Two modes:

  (no args)  PostToolUse hook on place_equity_order / cancel_equity_order.
             Reads the hook payload on stdin and alerts that an order was
             PLACED or CANCELLED, driven by the tool call itself.

  fills      Drains state/fills.jsonl and alerts on orders that actually
             FILLED. Invoked by run-agent.sh after the agent exits.

TRADING_PARAMETERS.md asks for a phone notification on every placed, filled,
or cancelled order. Fills used to be the gap: a fill happens at the broker
minutes or hours after the tool call, so no PostToolUse hook can ever see one,
and a placed limit order is not a trade -- it may never fill at all.

Rather than poll the broker from here, fills ride on work the agent already
does. Every run calls get_equity_orders and reconciles it against the journal,
so the agent already knows which orders filled; the prompt now also has it
append each newly-observed fill to state/fills.jsonl. This mode turns those
lines into alerts. No second API credential, no extra broker calls.

Consequences of that choice, both acceptable here:
  * Detection is run-granular, so an alert can lag a fill by up to the
    15-minute cadence. Since all_day_hours is disallowed and orders are gfd,
    fills can only occur inside 07:00-20:00 ET when runs are happening.
  * If a run dies before reconciling, its fills are reported by the next run,
    because seen-tracking is keyed on order_id rather than on time.

Transport is ntfy.sh: an HTTP POST to a topic URL, no account or API key.
The topic name is the only secret, so it is long and random and lives in
.env (gitignored), never in the repo.

Design rule: a notification must NEVER break trading. Every failure path
exits 0. If the topic is unconfigured, unreachable, or the payload is
malformed, the order still stands and we just lose the alert.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
FILLS_PATH = os.path.join(ROOT, "state", "fills.jsonl")
NOTIFIED_PATH = os.path.join(ROOT, "state", "fills-notified.json")
TIMEOUT_SECONDS = 6


def env_value(key):
    """Read `key` from the environment, falling back to parsing .env.

    The systemd service loads .env via EnvironmentFile so the variable is
    already present there, but an interactive session has no such loading —
    hence the file fallback.
    """
    value = os.environ.get(key, "").strip()
    if value:
        return value
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def header_safe(text):
    """HTTP headers must be latin-1 encodable; titles are user-facing text.

    An em dash or similar in a header raises UnicodeEncodeError deep inside
    http.client, which would otherwise crash the hook and violate the
    never-fail rule. Body text is sent as UTF-8 bytes and needs no scrubbing.
    """
    return text.encode("ascii", "replace").decode("ascii")


def mask(account):
    if not account or len(account) < 4:
        return "account"
    return f"••••{account[-4:]}"


def describe(tool_name, tool_input, tool_response):
    symbol = tool_input.get("symbol") or "?"
    side = (tool_input.get("side") or "").upper()
    qty = tool_input.get("quantity")
    price = tool_input.get("limit_price")
    dollars = tool_input.get("dollar_amount")

    if "cancel" in tool_name:
        title = f"Order cancelled - {symbol}"
        body = f"cancelled order {tool_input.get('order_id', '?')}"
        return title, body

    bits = []
    if qty and price:
        try:
            bits.append(f"{float(qty):g} @ ${float(price):,.2f}")
            bits.append(f"≈ ${float(qty) * float(price):,.2f}")
        except (TypeError, ValueError):
            bits.append(f"{qty} @ {price}")
    elif dollars:
        bits.append(f"${dollars}")

    session = tool_input.get("market_hours") or "regular_hours"
    bits.append(session)

    title = f"{side} {symbol}" if side else f"Order — {symbol}"
    body = " · ".join(bits)

    # Surface a rejection rather than implying the order stands.
    state = None
    if isinstance(tool_response, dict):
        data = tool_response.get("data") or tool_response
        if isinstance(data, dict):
            state = data.get("state") or (data.get("order") or {}).get("state")
    if state:
        body += f"\nstate: {state}"
    body += f"\n{mask(tool_input.get('account_number'))}"
    return title, body


def send(url, token, title, body, tags="chart_with_upwards_trend",
         priority="default"):
    """POST one notification. Returns True on success, never raises.

    Broad except on purpose: a missed alert is acceptable, a crashed hook on a
    live order is not. Anything at all goes to stderr.
    """
    try:
        headers = {
            "Title": header_safe(title),
            "Priority": priority,
            "Tags": tags,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            resp.read()
        return True
    except Exception as exc:  # noqa: BLE001 — see comment above
        print(f"notify: send failed ({exc.__class__.__name__}: {exc})", file=sys.stderr)
        return False


def describe_fill(fill):
    """Build the alert for one filled order.

    A fill is the thing that actually moved money, so it says so plainly and
    leads with realised P/L on a sell -- that is the number worth reading on a
    phone screen.
    """
    symbol = fill.get("symbol") or "?"
    side = (fill.get("side") or "").upper()
    qty = fill.get("quantity")
    price = fill.get("average_price")

    bits = []
    try:
        p = float(price)
        notional = float(qty) * p
        # Two decimals normally, four only when the average fill really has
        # sub-cent precision. Never string-munge a formatted number -- a naive
        # trailing-zero strip mangles prices like 10,000.0000.
        price_s = f"{p:,.2f}" if round(p, 2) == round(p, 6) else f"{p:,.4f}"
        bits.append(f"{float(qty):g} @ ${price_s}")
        bits.append(f"= ${notional:,.2f}")
    except (TypeError, ValueError):
        bits.append(f"{qty} @ {price}")

    title = f"FILLED {side} {symbol}".strip()

    # P/L only means something on the way out, and only if the agent recorded
    # what the position cost. Absent or unparseable -> just omit it.
    pnl = fill.get("pnl_pct")
    tags = "chart_with_upwards_trend"
    if side == "SELL" and pnl is not None:
        try:
            pnl = float(pnl)
            bits.append(f"P/L {pnl:+.2f}%")
            title = f"FILLED SELL {symbol} {pnl:+.2f}%"
            tags = "white_check_mark" if pnl >= 0 else "small_red_triangle_down"
        except (TypeError, ValueError):
            pass

    body = " · ".join(bits)
    if fill.get("filled_at"):
        body += f"\n{fill['filled_at']}"
    if fill.get("note"):
        body += f"\n{fill['note']}"
    return title, body, tags


def load_notified():
    """Return already-alerted order ids, oldest first.

    Kept as an ordered list, not a set: the file is trimmed to a bounded length
    on save, and trimming an unordered set would evict arbitrary ids -- possibly
    the newest -- causing duplicate texts for recent fills.
    """
    try:
        with open(NOTIFIED_PATH) as f:
            data = json.load(f)
        ids = data.get("order_ids") or []
        return [str(i) for i in ids] if isinstance(ids, list) else []
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        # A missing or corrupt seen-file must not wedge alerting. Worst case we
        # re-send an alert once; that is strictly better than going silent.
        return []


def save_notified(order_ids):
    try:
        os.makedirs(os.path.dirname(NOTIFIED_PATH), exist_ok=True)
        tmp = f"{NOTIFIED_PATH}.tmp"
        with open(tmp, "w") as f:
            # Bounded: keep the 500 most recent, dropping from the front.
            json.dump({"order_ids": order_ids[-500:]}, f, indent=2)
        os.replace(tmp, NOTIFIED_PATH)
    except OSError as exc:
        print(f"notify: could not persist seen-set ({exc})", file=sys.stderr)


def run_fills_mode(url, token):
    """Alert on each fill in state/fills.jsonl not already alerted on.

    Dedupe is by order_id and survives restarts, so re-running this is safe and
    a fill re-reported by a later run does not text twice.
    """
    try:
        with open(FILLS_PATH) as f:
            lines = [l.strip() for l in f if l.strip()]
    except OSError:
        return  # no fills recorded — the normal case

    notified = load_notified()
    seen = set(notified)
    added = False
    for line in lines:
        try:
            fill = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            print(f"notify: skipping malformed fill line: {line[:120]}", file=sys.stderr)
            continue
        oid = str(fill.get("order_id")
                  or f"{fill.get('symbol')}|{fill.get('filled_at')}")
        if oid in seen:
            continue
        title, body, tags = describe_fill(fill)
        if send(url, token, title, body, tags=tags, priority="high"):
            notified.append(oid)
            seen.add(oid)
            added = True
        else:
            # Leave it unmarked so the next run retries rather than silently
            # dropping a fill alert on a transient network failure.
            print(f"notify: will retry fill {oid} next run", file=sys.stderr)
    if added:
        save_notified(notified)


def main():
    url = env_value("NOTIFY_URL")
    if not url:
        print("notify: NOTIFY_URL unset — skipping", file=sys.stderr)
        sys.exit(0)
    # Optional: an ntfy access token, needed for reserved topics or any
    # instance that denies anonymous publishing.
    token = env_value("NOTIFY_TOKEN")

    if len(sys.argv) > 1 and sys.argv[1] == "fills":
        run_fills_mode(url, token)
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    tool_name = payload.get("tool_name") or ""
    title, body = describe(tool_name, tool_input, payload.get("tool_response"))
    send(url, token, title, body)
    sys.exit(0)


if __name__ == "__main__":
    main()
