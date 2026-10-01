#!/usr/bin/env bash
# Install the systemd units for the weekly review (scripts/weekly-review.py)
# and the rule-change proposals that follow it (scripts/propose-rules.sh).
#
# Fridays 16:30 ET: after the last run of the week (15:30) has finished and
# its trade record is rebuilt. Persistent=true so a VM that was down on Friday
# runs the review when it comes back instead of skipping the week.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

sudo tee /etc/systemd/system/rh-weekly-review@.service >/dev/null <<UNIT
[Unit]
Description=Weekly trading review for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=%i
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${REPO_DIR}/.env
# claude lives in ~/.local/bin, which systemd's default PATH omits (same as
# robinhood-agent@.service). Needed by propose-rules.sh.
Environment=PATH=/home/%i/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
# Rebuild the trade record first, so the review sees the latest fills. The
# leading '-' keeps a failed rebuild (e.g. Robinhood unreachable for the
# context fetch) from blocking the review of the record that already exists.
ExecStartPre=-/usr/bin/python3 ${REPO_DIR}/scripts/trade-record.py
ExecStart=/usr/bin/python3 ${REPO_DIR}/scripts/weekly-review.py
# Then draft rule-change proposals -- only if the review raised flags, so a
# quiet week costs no model call. '-' so a failed draft never fails the review.
ExecStart=-${REPO_DIR}/scripts/propose-rules.sh
TimeoutStartSec=20min
StandardOutput=append:${REPO_DIR}/logs/weekly-review.log
StandardError=append:${REPO_DIR}/logs/weekly-review.log
UNIT

sudo tee /etc/systemd/system/rh-weekly-review@.timer >/dev/null <<UNIT
[Unit]
Description=Weekly trading review for %i, Fridays after the close

[Timer]
OnCalendar=Fri 16:30 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
UNIT

mkdir -p "${REPO_DIR}/logs"
# systemd creates append: targets as root; keep the log user-owned.
touch "${REPO_DIR}/logs/weekly-review.log"
sudo chown "${USER_NAME}:${USER_NAME}" "${REPO_DIR}/logs/weekly-review.log"

sudo systemctl daemon-reload
sudo systemctl enable --now "rh-weekly-review@${USER_NAME}.timer"

echo
echo "Installed. Next run:"
systemctl list-timers "rh-weekly-review@${USER_NAME}.timer" --no-pager
