#!/usr/bin/env bash
# Install the systemd units that back up state/ to GCS nightly.
#
# Usage:  ./scripts/install-gcs-backup.sh <bucket-name> [prefix]
#
# Runs at 21:00 UTC (17:00 ET) -- after the 15:30 ET run, the last of the
# trading day, so the snapshot always contains a complete day of entries.
# Persistent=true so a VM that was down at 21:00 still backs up on boot rather
# than silently losing that day's snapshot.
set -euo pipefail

BUCKET="${1:-}"
PREFIX="${2:-robinhood-agent}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

if [ -z "$BUCKET" ]; then
  echo "usage: $0 <bucket-name> [prefix]" >&2
  exit 64
fi

sudo tee /etc/systemd/system/rh-gcs-backup@.service >/dev/null <<EOF
[Unit]
Description=Back up robinhood agent state to GCS for %i
After=network-online.target
Wants=network-online.target
OnFailure=rh-gcs-backup-failed@%i.service

[Service]
Type=oneshot
User=%i
Environment=GCS_BACKUP_BUCKET=${BUCKET}
Environment=GCS_BACKUP_PREFIX=${PREFIX}
Environment=REPO_DIR=${REPO_DIR}
# flock against the agent: journal.md is appended to at the end of a run, and
# rsyncing mid-append would ship a torn final entry.
ExecStart=/usr/bin/flock -w 120 ${REPO_DIR}/state/.gcs-backup.lock \\
    ${REPO_DIR}/scripts/backup-journal-gcs.sh
StandardOutput=append:${REPO_DIR}/logs/gcs-backup.log
StandardError=append:${REPO_DIR}/logs/gcs-backup.log
EOF

sudo tee /etc/systemd/system/rh-gcs-backup@.timer >/dev/null <<EOF
[Unit]
Description=Nightly GCS backup of robinhood agent state for %i

[Timer]
OnCalendar=*-*-* 21:00:00 UTC
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

# Same failure-marker pattern as token refresh: leave something under state/
# that the agent can see at the start of a run, plus a loud log line.
sudo tee /etc/systemd/system/rh-gcs-backup-failed@.service >/dev/null <<EOF
[Unit]
Description=Record a failed GCS backup for %i

[Service]
Type=oneshot
User=%i
ExecStart=/bin/bash -c 'ts=\$(date -u +%%FT%%TZ); echo "\$ts GCS BACKUP FAILED - see gcs-backup.log"; echo "\$ts gcs backup failed" > ${REPO_DIR}/state/GCS_BACKUP_FAILED'
StandardOutput=append:${REPO_DIR}/logs/gcs-backup.log
StandardError=append:${REPO_DIR}/logs/gcs-backup.log
EOF

mkdir -p "${REPO_DIR}/logs" "${REPO_DIR}/state"
touch "${REPO_DIR}/logs/gcs-backup.log"
sudo chown "${USER_NAME}:${USER_NAME}" "${REPO_DIR}/logs/gcs-backup.log"

sudo systemctl daemon-reload
sudo systemctl enable --now "rh-gcs-backup@${USER_NAME}.timer"

echo
echo "Installed. Next run:"
systemctl list-timers "rh-gcs-backup@${USER_NAME}.timer" --no-pager
