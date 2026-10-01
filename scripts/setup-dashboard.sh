#!/usr/bin/env bash
# Install the read-only trading dashboard and publish it through a Cloudflare
# Tunnel. Idempotent: safe to re-run.
#
#   sudo ./scripts/setup-dashboard.sh            # install + guide the tunnel
#   sudo ./scripts/setup-dashboard.sh --local    # dashboard only, no tunnel
#
# WHY A TUNNEL RATHER THAN AN OPEN PORT. cloudflared dials OUT from this VM and
# holds the connection open; Cloudflare forwards requests back down it. No
# inbound port is opened, no GCP firewall rule is added, and the VM's external
# IP is never a target. The alternative -- binding 0.0.0.0 and opening a port --
# puts a brokerage dashboard on the public internet behind whatever auth the
# app implements, and it starts getting scanned within minutes.
#
# The two controls hold each other up:
#   * the dashboard binds 127.0.0.1, so the ONLY route in is the tunnel;
#   * Cloudflare Access authenticates at the edge before forwarding, and
#     stamps Cf-Access-Authenticated-User-Email, which server.py checks.
# Neither is sufficient alone. Do not "temporarily" bind 0.0.0.0 to debug --
# that turns the identity header into something anyone can forge.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
PORT=8080
LOCAL_ONLY=0
[[ "${1:-}" == "--local" ]] && LOCAL_ONLY=1

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo: sudo $0 ${1:-}" >&2
  exit 1
fi

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# Guard the thing that actually bit during development: cloudflared is a ~50MB
# download and this VM has run out of disk before. Failing here with a clear
# message beats a half-installed package.
avail_mb=$(df -Pm / | awk 'NR==2{print $4}')
if [[ $LOCAL_ONLY -eq 0 && $avail_mb -lt 300 ]]; then
  echo "only ${avail_mb}MB free on / -- free some space before installing" >&2
  echo "  sudo journalctl --vacuum-size=200M" >&2
  echo "  sudo logrotate -f /etc/logrotate.conf" >&2
  exit 1
fi

# ---------------------------------------------------------------- dashboard
say "1. Installing the dashboard service"
install -o "$SERVICE_USER" -g "$SERVICE_USER" -d "$REPO_DIR/logs"
install -m 644 "$REPO_DIR/systemd/robinhood-dashboard.service" \
               /etc/systemd/system/robinhood-dashboard@.service
systemctl daemon-reload
systemctl enable --now "robinhood-dashboard@${SERVICE_USER}.service"
sleep 2

if curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
  echo "   dashboard healthy on 127.0.0.1:${PORT}"
else
  echo "   dashboard did NOT come up. Recent log:" >&2
  journalctl -u "robinhood-dashboard@${SERVICE_USER}" -n 25 --no-pager >&2 || true
  exit 1
fi

if [[ $LOCAL_ONLY -eq 1 ]]; then
  say "Done (local only)."
  cat <<EOF
Reach it from your laptop over SSH, with nothing exposed:

  gcloud compute ssh trading-agent --zone us-west1-b -- -L ${PORT}:localhost:${PORT}
  then open http://localhost:${PORT}

Re-run without --local to publish it through Cloudflare.
EOF
  exit 0
fi

# --------------------------------------------------------------- cloudflared
say "2. Installing cloudflared"
if ! command -v cloudflared >/dev/null; then
  arch="$(dpkg --print-architecture)"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL -o "$tmp/cloudflared.deb" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  dpkg -i "$tmp/cloudflared.deb" >/dev/null
  echo "   installed $(cloudflared --version 2>&1 | head -1)"
else
  echo "   already present: $(cloudflared --version 2>&1 | head -1)"
fi

# The remaining steps need a browser login and a choice of hostname, so they
# are printed rather than run. Doing them non-interactively would mean holding
# a Cloudflare API token on the VM, which is a worse trade than a five-minute
# manual step performed once.
say "3. Remaining steps -- these need your browser, so run them yourself"
cat <<EOF

  a. Authenticate this VM to your Cloudflare account. Prints a link; open it
     and pick the domain you want to use.

       cloudflared tunnel login

  b. Create the tunnel and point a hostname at the local dashboard. Replace
     trading.example.com with a hostname on your domain.

       cloudflared tunnel create trading-dashboard
       cloudflared tunnel route dns trading-dashboard trading.example.com

  c. Write the config (substitute the UUID that 'create' printed):

       sudo mkdir -p /etc/cloudflared
       sudo tee /etc/cloudflared/config.yml >/dev/null <<'YAML'
       tunnel: trading-dashboard
       credentials-file: /root/.cloudflared/<TUNNEL-UUID>.json
       ingress:
         - hostname: trading.example.com
           service: http://127.0.0.1:${PORT}
         - service: http_status:404
       YAML

  d. Run it as a service. Install the unit from THIS REPO -- do not use
     'cloudflared service install'. That command writes a unit that runs
     'tunnel run --token-file', which is remotely-managed mode: it ignores the
     config.yml you just wrote in step c and answers 503 to every request. The
     only symptom is one WRN line, "No ingress rules were defined", in the
     service log. This exact trap cost hours during setup.

       sudo install -m 644 systemd/cloudflared.service \\
                           /etc/systemd/system/cloudflared.service
       sudo systemctl daemon-reload
       sudo systemctl enable --now cloudflared

     Then confirm the ingress actually loaded -- 'active' is not enough:

       sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
       sudo journalctl -u cloudflared -n 50 | grep -c 'No ingress rules'   # want 0

  e. LOCK IT DOWN -- do this BEFORE you browse to the hostname. Until an
     Access policy exists, that URL is public to anyone who guesses it.

       Cloudflare dashboard -> Zero Trust -> Access -> Applications
         Add an application -> Self-hosted
         Application domain : trading.example.com
         Policy name        : owner-only
         Action             : Allow
         Include            : Emails -> (your address)
         Authentication     : One-time PIN

     That address must match DASHBOARD_CF_EMAIL in
     /etc/systemd/system/robinhood-dashboard@.service, currently:

       $(grep -o 'DASHBOARD_CF_EMAIL=.*' "$REPO_DIR/systemd/robinhood-dashboard.service" || echo '(unset)')

     With both in place, Cloudflare emails you a one-time code AND the
     dashboard independently refuses any request lacking a matching verified
     identity.

  f. Verify the lock holds, from a browser where you are NOT signed in (a
     private window is enough):

       curl -sI https://trading.example.com | head -1

     Expect a 302 to a Cloudflare login. Anything else means the edge is not
     authenticating and you are relying on the app check alone:

       200  Access policy missing AND DASHBOARD_CF_EMAIL unset. Wide open.
       403  The tunnel works and server.py refused you -- but the refusal came
            from the ORIGIN, not the edge, so no Access policy is attached.
            Not "secure enough": the whole reason the identity header can be
            trusted is that Access sets it and overwrites any client copy.
            With no policy in front, nothing is doing that overwriting.
       503  Ingress not loaded -- see step d, you are probably in token mode.

EOF

say "Dashboard service is up. The Cloudflare steps above are still yours to run."
