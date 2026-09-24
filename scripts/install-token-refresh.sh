#!/usr/bin/env bash
# Install the systemd units that keep the robinhood-trading MCP token alive.
#
# The access token lasts ~7.8 days and renews via a browser-less refresh grant.
# Checking daily against a 96h threshold gives roughly four days of retries
# before an expiry could ever bite, so a few failed ticks are survivable.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

sudo tee /etc/systemd/system/rh-token-refresh@.service >/dev/null <<EOF
[Unit]
Description=Refresh the robinhood-trading MCP OAuth token for %i
After=network-online.target
Wants=network-online.target
OnFailure=rh-token-refresh-failed@%i.service

[Service]
Type=oneshot
User=%i
# flock serialises against concurrent agent runs: Robinhood rotates the refresh
# token on use, so two overlapping refreshes would leave one holding a dead one.
ExecStart=/usr/bin/flock -w 60 ${REPO_DIR}/state/.token-refresh.lock \\
    /usr/bin/python3 ${REPO_DIR}/scripts/refresh-rh-token.py --threshold-hours 96
StandardOutput=append:${REPO_DIR}/logs/token-refresh.log
StandardError=append:${REPO_DIR}/logs/token-refresh.log
EOF

sudo tee /etc/systemd/system/rh-token-refresh@.timer >/dev/null <<EOF
[Unit]
Description=Daily refresh check for the robinhood-trading MCP token for %i

[Timer]
OnCalendar=*-*-* 11:00:00 UTC
# Catch up after VM downtime instead of silently skipping the missed window.
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
EOF

# Failure path: leave a marker the agent can see and a loud log line. Logging
# goes through systemd's append: rather than tee, because systemd opens that
# file as root while ExecStart runs as %i -- tee would hit permission denied.
sudo tee /etc/systemd/system/rh-token-refresh-failed@.service >/dev/null <<EOF
[Unit]
Description=Record a failed robinhood-trading token refresh for %i

[Service]
Type=oneshot
User=%i
ExecStart=/bin/bash -c 'ts=\$(date -u +%%FT%%TZ); echo "\$ts TOKEN REFRESH FAILED - interactive re-auth required"; echo "\$ts token refresh failed" > ${REPO_DIR}/state/TOKEN_REFRESH_FAILED'
StandardOutput=append:${REPO_DIR}/logs/token-refresh.log
StandardError=append:${REPO_DIR}/logs/token-refresh.log
EOF

mkdir -p "${REPO_DIR}/logs" "${REPO_DIR}/state"
# systemd creates append: targets as root; keep it user-owned so manual runs of
# refresh-rh-token.py can append to the same log.
touch "${REPO_DIR}/logs/token-refresh.log"
sudo chown "${USER_NAME}:${USER_NAME}" "${REPO_DIR}/logs/token-refresh.log"

sudo systemctl daemon-reload
sudo systemctl enable --now "rh-token-refresh@${USER_NAME}.timer"

echo
echo "Installed. Next run:"
systemctl list-timers "rh-token-refresh@${USER_NAME}.timer" --no-pager
