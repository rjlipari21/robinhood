#!/usr/bin/env python3
"""HTTP server for the trading dashboard. Read-only, localhost-bound.

    python3 webapp/server.py [--port 8080] [--host 127.0.0.1]

WHY IT BINDS TO LOCALHOST. The dashboard is published through a Cloudflare
Tunnel: cloudflared runs on this VM, dials OUT to Cloudflare, and forwards
requests back to 127.0.0.1. Nothing inbound is ever opened, the VM's external
IP stays dark, and no GCP firewall rule is needed. Binding 0.0.0.0 would throw
that away and put an account dashboard on the public internet, so --host exists
but defaults to loopback and should stay there.

AUTHENTICATION HAPPENS AT CLOUDFLARE'S EDGE. Cloudflare Access checks identity
before a request is ever forwarded, so unauthenticated traffic never reaches
this process. That makes the edge the primary control and this file's job is
to not undermine it. As a second layer, set DASHBOARD_CF_EMAIL to the address
Access is configured to allow: each request must then carry a matching
Cf-Access-Authenticated-User-Email header, which Cloudflare sets itself and
overwrites on any client-supplied copy. That header is only trustworthy
BECAUSE the origin is unreachable except through the tunnel -- the two controls
hold each other up, which is why the localhost bind is not optional in
practice.

The server is GET/HEAD only and imports a data layer that can reach exactly
five read-only Robinhood tools, so there is no request that can move money.
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect                                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_EMAIL = os.environ.get("DASHBOARD_CF_EMAIL", "").strip().lower()

# No external origins at all: everything is same-origin or inline, so the page
# cannot exfiltrate account data to a third party even if markdown in the
# journal were somehow crafted to try.
CSP = ("default-src 'none'; "
       "style-src 'unsafe-inline'; "
       "script-src 'unsafe-inline'; "
       "img-src data:; "
       "connect-src 'self'; "
       "base-uri 'none'; "
       "form-action 'none'; "
       "frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "trading-dashboard"
    sys_version = ""                       # do not advertise the Python version

    # ---- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        # Deliberately terse: no query strings, no headers, no identity. The
        # journal already records what the agent did; an access log of a
        # financial dashboard is a liability with no operational value here.
        sys.stderr.write("%s %s %s\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            self.command, self.path.split("?")[0]))

    def _headers(self, code, ctype, body_len, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(body_len))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._headers(code, ctype, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorised(self) -> bool:
        if not REQUIRED_EMAIL:
            return True
        got = (self.headers.get("Cf-Access-Authenticated-User-Email") or "")
        return got.strip().lower() == REQUIRED_EMAIL

    # ---- routes -----------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == "/healthz":              # no auth: tunnel liveness probe
            self._send(200, "text/plain; charset=utf-8", "ok\n")
            return

        if not self._authorised():
            self._send(403, "text/plain; charset=utf-8",
                       "forbidden: Cloudflare Access identity missing or "
                       "does not match DASHBOARD_CF_EMAIL\n")
            return

        if path == "/":
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as fh:
                    self._send(200, "text/html; charset=utf-8", fh.read())
            except OSError:
                self._send(500, "text/plain; charset=utf-8", "index.html missing\n")
            return

        if path == "/api/snapshot":
            days = 7
            if "?" in self.path:
                for part in self.path.split("?", 1)[1].split("&"):
                    k, _, v = part.partition("=")
                    if k == "days" and v.isdigit():
                        days = max(1, min(90, int(v)))
            try:
                payload = collect.snapshot(days)
            except Exception as exc:        # never leak a traceback to the browser
                self.log_message("snapshot failed: %s", type(exc).__name__)
                payload = {"fatal": f"{type(exc).__name__} while collecting data"}
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(payload, default=str))
            return

        self._send(404, "text/plain; charset=utf-8", "not found\n")

    # Any other verb is rejected outright -- this app has no writes.
    def _deny(self):
        self._send(405, "text/plain; charset=utf-8", "read-only\n")

    do_POST = do_PUT = do_DELETE = do_PATCH = _deny


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1",
                    help="keep this on loopback; the tunnel reaches it locally")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    guard = (f"Cf-Access email must equal {REQUIRED_EMAIL}"
             if REQUIRED_EMAIL else
             "no origin-side identity check (relying on Cloudflare Access alone)")
    print(f"dashboard on http://{args.host}:{args.port}  [{guard}]", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
