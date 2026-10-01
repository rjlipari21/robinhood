#!/usr/bin/env bash
# Install log rotation and per-run log pruning for the trading agent.
#
# Two mechanisms, because there are two shapes of problem:
#
#   logrotate  -- for logs that grow without bound (robinhood-sync.log,
#                 token-refresh.log, gcs-backup.log, dashboard.log)
#   cron find  -- for logs/run-YYYY-MM-DD.log, which never grow individually
#                 but accumulate one file per trading day forever. logrotate
#                 is the wrong tool: its maxage only prunes files it rotated
#                 itself, and rotating dated files just yields run-....log.1.gz
#
# RUN_LOG_KEEP_DAYS is deliberately generous. The run logs are the only record
# of what a run did when the journal write itself failed, which is exactly the
# case you want to investigate months later.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
RUN_LOG_KEEP_DAYS="${RUN_LOG_KEEP_DAYS:-120}"

echo "==> installing /etc/logrotate.d/robinhood"
sudo install -m 0644 -o root -g root \
  "${REPO_DIR}/config/logrotate-robinhood" /etc/logrotate.d/robinhood

echo "==> validating the config (dry run, changes nothing)"
# Only real errors are fatal. "does not exist -- skipping" is the expected
# output of missingok for a log whose service is not installed yet
# (gcs-backup.log until install-gcs-backup.sh runs), so it must not fail here.
if sudo logrotate -d /etc/logrotate.d/robinhood 2>&1 | grep -iE '^error|error:' ; then
  echo "logrotate reported errors above" >&2
  exit 1
fi
echo "    config parses clean"

echo "==> installing the run-log prune cron entry (daily 03:20)"
PRUNE="20 3 * * * find ${REPO_DIR}/logs -maxdepth 1 -name 'run-*.log' -type f -mtime +${RUN_LOG_KEEP_DAYS} -delete"
# Replace any previous version of this line rather than stacking duplicates on
# repeated installs.
( crontab -l 2>/dev/null | grep -vF "name 'run-*.log'" ; echo "$PRUNE" ) | crontab -

echo
echo "Installed. Current state:"
echo "  run logs:  $(find "${REPO_DIR}/logs" -maxdepth 1 -name 'run-*.log' | wc -l) files, $(du -sh "${REPO_DIR}/logs" | cut -f1) total"
echo "  keeping:   ${RUN_LOG_KEEP_DAYS} days of run logs"
echo
crontab -l | grep -F "run-*.log"
