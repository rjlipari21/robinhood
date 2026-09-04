#!/usr/bin/env python3
"""Renew the robinhood-trading MCP OAuth token without a browser.

The MCP access token lives ~7 days; the refresh grant is a public client
(token_endpoint_auth_methods_supported: ["none"]), so renewal needs no secret
and no human. Run this on a timer well inside the access-token window and the
credential stays valid indefinitely -- for as long as Robinhood honours the
refresh token.

Exit codes are meaningful, because the timer alerts on them:
  0  refreshed, or still fresh and nothing to do
  2  no stored credential (never authorised, or the entry was dropped)
  3  refresh rejected -- the chain is broken and needs interactive re-auth
  4  transport/parse failure -- transient, safe to retry next tick
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CREDS = os.path.expanduser("~/.claude/.credentials.json")
TOKEN_URL = "https://api.robinhood.com/oauth2/token/"
SERVER_MATCH = "robinhood-trading"
BACKUP_KEEP = 5


def log(msg: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {msg}", flush=True)


def load_creds() -> dict:
    with open(CREDS) as fh:
        return json.load(fh)


def find_entry(creds: dict) -> tuple[str, dict] | tuple[None, None]:
    """Locate the robinhood-trading entry. Its key embeds a server-url hash."""
    for key, val in creds.get("mcpOAuth", {}).items():
        if SERVER_MATCH in key:
            return key, val
    return None, None


def refresh(client_id: str, refresh_token: str, scope: str) -> dict:
    """Exchange the refresh token. Tries form encoding, falls back to JSON."""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "scope": scope or "internal",
    }
    attempts = (
        ("application/x-www-form-urlencoded", urllib.parse.urlencode(payload).encode()),
        ("application/json", json.dumps(payload).encode()),
    )
    last = None
    for ctype, body in attempts:
        req = urllib.request.Request(
            TOKEN_URL,
            data=body,
            method="POST",
            headers={"Content-Type": ctype, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            last = RuntimeError(f"HTTP {exc.code} ({ctype}): {detail}")
            # 4xx means the server understood and refused -- a different encoding
            # will not help, except a 415 which is specifically about encoding.
            if 400 <= exc.code < 500 and exc.code != 415:
                raise last from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
    raise last if last else RuntimeError("refresh failed with no diagnostic")


def persist(creds: dict, key: str, entry: dict, tok: dict) -> None:
    """Atomically write the rotated credential back, keeping a backup.

    Written to a temp file in the same directory and renamed, so a crash mid-write
    cannot leave a truncated credential file behind.
    """
    entry["accessToken"] = tok["access_token"]
    # Robinhood rotates the refresh token on use; a response that omits it means
    # the old one stays valid. Never clobber a good token with an absent one.
    if tok.get("refresh_token"):
        entry["refreshToken"] = tok["refresh_token"]
    if tok.get("expires_in"):
        entry["expiresAt"] = int(time.time() * 1000) + int(tok["expires_in"]) * 1000
    if tok.get("scope"):
        entry["scope"] = tok["scope"]
    creds["mcpOAuth"][key] = entry

    if os.path.exists(CREDS):
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(CREDS, f"{CREDS}.bak-{stamp}")
        backups = sorted(
            f for f in os.listdir(os.path.dirname(CREDS))
            if f.startswith(os.path.basename(CREDS) + ".bak-")
        )
        for stale in backups[:-BACKUP_KEEP]:
            os.unlink(os.path.join(os.path.dirname(CREDS), stale))

    tmp = f"{CREDS}.tmp-{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(creds, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, CREDS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--threshold-hours", type=float, default=48.0,
        help="refresh when the access token expires within this window",
    )
    ap.add_argument("--force", action="store_true", help="refresh regardless of age")
    ap.add_argument(
        "--check-only", action="store_true",
        help="report remaining lifetime and exit; never contacts Robinhood",
    )
    args = ap.parse_args()

    if not os.path.exists(CREDS):
        log(f"FATAL no credential file at {CREDS}")
        return 2
    try:
        creds = load_creds()
    except (OSError, json.JSONDecodeError) as exc:
        log(f"FATAL cannot read credential file: {exc}")
        return 4

    key, entry = find_entry(creds)
    if not entry:
        log(f"FATAL no '{SERVER_MATCH}' entry under mcpOAuth -- needs interactive auth")
        return 2

    expires_at = int(entry.get("expiresAt", 0))
    remaining_h = (expires_at / 1000 - time.time()) / 3600
    expiry_str = dt.datetime.fromtimestamp(
        expires_at / 1000, dt.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"access token expires {expiry_str} ({remaining_h:.1f}h remaining)")

    if args.check_only:
        return 0 if remaining_h > args.threshold_hours else 3

    if remaining_h > args.threshold_hours and not args.force:
        log(f"still fresh (>{args.threshold_hours}h) -- nothing to do")
        return 0

    if not entry.get("refreshToken"):
        log("FATAL no refresh token stored -- needs interactive re-auth")
        return 3

    log("refreshing...")
    try:
        tok = refresh(entry["clientId"], entry["refreshToken"], entry.get("scope", ""))
    except RuntimeError as exc:
        log(f"FATAL refresh rejected: {exc}")
        log("the refresh chain is broken -- interactive re-auth required")
        return 3
    except Exception as exc:  # transport, DNS, timeout
        log(f"ERROR refresh failed (transient?): {exc}")
        return 4

    if not tok.get("access_token"):
        log(f"FATAL response had no access_token: {json.dumps(tok)[:300]}")
        return 3

    persist(creds, key, entry, tok)
    new_h = (int(entry["expiresAt"]) / 1000 - time.time()) / 3600
    rotated = "rotated" if tok.get("refresh_token") else "unchanged"
    log(f"OK refreshed -- valid {new_h:.1f}h, refresh token {rotated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
