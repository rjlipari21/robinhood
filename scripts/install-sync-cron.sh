#!/usr/bin/env bash
# Installs a cron job on this machine that runs sync-vm.sh every 5 minutes,
# logging to ~/robinhood-sync.log. Run this ONCE on the GCP VM.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/robinhood}"
SCRIPT_PATH="$REPO_DIR/scripts/sync-vm.sh"
LOG_PATH="$HOME/robinhood-sync.log"
CRON_LINE="*/5 * * * * REPO_DIR=$REPO_DIR $SCRIPT_PATH >> $LOG_PATH 2>&1"

chmod +x "$SCRIPT_PATH"

if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab not found — installing cron..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y cron
    sudo systemctl enable --now cron
  else
    echo "ERROR: crontab is not installed and this isn't a Debian/apt system." >&2
    echo "Install a cron daemon manually, then re-run this script." >&2
    exit 1
  fi
fi

# crontab -l exits non-zero when the user has no crontab yet — that's the
# normal case on a fresh VM, not an error, so it's caught explicitly with
# `|| true` rather than left to set -e (which would otherwise abort the
# script here with no message).
existing_crontab="$(crontab -l 2>/dev/null || true)"
filtered_crontab="$(printf '%s\n' "$existing_crontab" | grep -vF "$SCRIPT_PATH" || true)"

# Replace any prior sync-vm.sh entry, then add the current one.
{
  [ -n "$filtered_crontab" ] && printf '%s\n' "$filtered_crontab"
  printf '%s\n' "$CRON_LINE"
  true
} | crontab -

# Verify it actually landed — a silent crontab failure should not report success.
if ! crontab -l 2>/dev/null | grep -qF "$SCRIPT_PATH"; then
  echo "ERROR: crontab install did not take effect. Check 'crontab -l' manually." >&2
  exit 1
fi

echo "Installed. The VM will run 'git pull' on $REPO_DIR every 5 minutes."
echo "Verify with: crontab -l"
echo "Check progress with: tail -f $LOG_PATH"
echo "Remove later with: crontab -l | grep -vF '$SCRIPT_PATH' | crontab -"
