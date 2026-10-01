#!/usr/bin/env bash
# Back up the agent's runtime state to Google Cloud Storage.
#
# Why this exists: state/ is gitignored on purpose -- sync-vm.sh pulls this
# branch every 5 minutes under `set -euo pipefail`, so a tracked, agent-written
# journal would dirty the tree and break the sync. That keeps the journal off
# git, which leaves it on one VM disk with no copy anywhere. The 2026-09-08
# disk-full event that broke token refresh for 14 days is the precedent: the
# journal, its archives and the ledger all share that single failure domain.
#
# What is backed up: journal.md (the durable narrative), archive/ (older
# months), ledger.json (hook-written order record) and fills.jsonl (the alert
# queue). Everything else under state/ is scratch or a marker file and is
# deliberately excluded.
#
# Two things land in the bucket on each run:
#   <prefix>/latest/...            mirror of current state, overwritten
#   <prefix>/daily/YYYY-MM-DD/...  dated snapshot, written once per day
# The snapshot is what makes this a backup rather than a mirror: journal.md is
# append-only, so a truncation or corruption would otherwise propagate to the
# only copy on the next run. Enable object versioning on the bucket too.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BUCKET="${GCS_BACKUP_BUCKET:-}"
PREFIX="${GCS_BACKUP_PREFIX:-robinhood-agent}"
MARKER="${REPO_DIR}/state/GCS_BACKUP_FAILED"

ts() { date -u +%FT%TZ; }
fail() { echo "$(ts) GCS BACKUP FAILED - $1"; echo "$(ts) $1" > "$MARKER"; exit 1; }

[ -n "$BUCKET" ] || fail "GCS_BACKUP_BUCKET is unset (set it in the systemd unit or the environment)"

cd "$REPO_DIR"
[ -f state/journal.md ] || fail "state/journal.md not found under $REPO_DIR"

DEST="gs://${BUCKET}/${PREFIX}"
DAY="$(TZ=America/New_York date +%F)"

# Preflight. A read-only scope or a missing bucket fails here with one clear
# line rather than halfway through, leaving a partial snapshot behind.
gcloud storage ls "gs://${BUCKET}" >/dev/null 2>&1 \
  || fail "cannot reach gs://${BUCKET} -- check the bucket exists and the VM has devstorage.read_write scope"

# Mirror. --delete-unmatched-destination-objects keeps archived months from
# lingering in latest/ after archive-journal.py moves them out of journal.md.
gcloud storage rsync state "$DEST/latest" \
  --recursive --delete-unmatched-destination-objects \
  --exclude='.*/tmp/.*|.*\.lock$|.*/HALT$|.*_FAILED$|.*/fills-notified\.json$' \
  || fail "rsync to $DEST/latest failed"

# Dated snapshot, written once per ET day. Skipped silently if today's already
# exists, so running this more than once a day costs nothing.
if gcloud storage ls "$DEST/daily/$DAY/journal.md" >/dev/null 2>&1; then
  echo "$(ts) snapshot $DAY already present, mirror refreshed"
else
  gcloud storage cp state/journal.md "$DEST/daily/$DAY/journal.md" || fail "snapshot copy failed"
  gcloud storage cp state/ledger.json "$DEST/daily/$DAY/ledger.json" 2>/dev/null || true
  echo "$(ts) snapshot written to $DEST/daily/$DAY/"
fi

rm -f "$MARKER"
echo "$(ts) OK backed up $(wc -l < state/journal.md) journal lines to $DEST"
