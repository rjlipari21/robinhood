#!/usr/bin/env python3
"""Push notifications for order activity.

Wired as a PostToolUse hook on place_equity_order and cancel_equity_order.
TRADING_PARAMETERS.md asks for a phone notification on every placed,
filled, or cancelled order.

  placed / cancelled — covered here, driven by the tool call itself.
  filled             — NOT covered. A fill happens at the broker minutes or
                       hours later, so nothing in a PostToolUse hook can see
                       it. That needs a separate poller; see README.

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
TIMEOUT_SECONDS = 6


def notify_url():
    """NOTIFY_URL from the environment, falling back to parsing .env.

    The systemd service loads .env via EnvironmentFile so the variable is
    already present there, but an interactive session has no such loading —
    hence the file fallback.
    """
    url = os.environ.get("NOTIFY_URL", "").strip()
    if url:
        return url
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("NOTIFY_URL="):
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


def main():
    url = notify_url()
    if not url:
        print("notify: NOTIFY_URL unset — skipping", file=sys.stderr)
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    tool_name = payload.get("tool_name") or ""
    title, body = describe(tool_name, tool_input, payload.get("tool_response"))

    # Broad except on purpose: a missed alert is acceptable, a crashed hook
    # on a live order is not. Anything at all goes to stderr and exits 0.
    try:
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Title": header_safe(title),
                "Priority": "default",
                "Tags": "chart_with_upwards_trend",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001 — see comment above
        print(f"notify: send failed ({exc.__class__.__name__}: {exc})", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
