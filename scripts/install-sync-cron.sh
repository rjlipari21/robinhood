#!/usr/bin/env bash
# Installs a cron job on this machine that runs sync-vm.sh every 5 minutes,
# logging to ~/robinhood-sync.log. Run this ONCE on the GCP VM.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/robinhood}"
SCRIPT_PATH="$REPO_DIR/scripts/sync-vm.sh"
LOG_PATH="$HOME/robinhood-sync.log"
CRON_LINE="*/5 * * * * REPO_DIR=$REPO_DIR $SCRIPT_PATH >> $LOG_PATH 2>&1"

chmod +x "$SCRIPT_PATH"

# Replace any prior sync-vm.sh cron entry, then add the current one.
( crontab -l 2>/dev/null | grep -vF "$SCRIPT_PATH"; echo "$CRON_LINE" ) | crontab -

echo "Installed. The VM will run 'git pull' on $REPO_DIR every 5 minutes."
echo "Check progress with: tail -f $LOG_PATH"
echo "Remove later with: crontab -l | grep -vF '$SCRIPT_PATH' | crontab -"
