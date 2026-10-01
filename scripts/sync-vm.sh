#!/usr/bin/env bash
# Pull the latest trading strategy (TRADING_PARAMETERS.md, scan configs, etc.)
# from the active development branch. Run this manually, or install it as a
# cron job (see install-sync-cron.sh) to keep a GCP VM's checkout in sync
# with the strategy this session is actively trading with.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/robinhood}"
BRANCH="${BRANCH:-claude/robinhood-gcp-integration-banfc3}"

cd "$REPO_DIR"

BEFORE="$(git rev-parse HEAD)"
git fetch origin "$BRANCH"
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
git pull origin "$BRANCH"
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" != "$AFTER" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Updated $BEFORE -> $AFTER"
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Already up to date at $AFTER"
fi
