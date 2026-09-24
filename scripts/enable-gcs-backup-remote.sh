#!/usr/bin/env bash
# Create the backup bucket and grant the trading VM write access to it.
#
# RUN THIS FROM YOUR LAPTOP OR CLOUD SHELL -- NOT FROM THE VM.
# Changing instance scopes requires the instance to be STOPPED, so a run from
# trading-agent itself would halt the machine executing the command with
# nothing left to start it again. The guard below refuses to run there.
#
# Run it only AFTER the 09:30 ET run has placed the protective exits on RVMD
# and NWS: the VM is down for a minute or two here, and a stop that overlaps a
# scheduled run silently skips it.
#
# Usage:  ./scripts/enable-gcs-backup-remote.sh <bucket-name> [project-id]
set -euo pipefail

BUCKET="${1:-}"
PROJECT="${2:-project-002d01fe-4b42-43e8-a2a}"
VM_NAME="${VM_NAME:-trading-agent}"
ZONE="${ZONE:-us-west1-b}"
LOCATION="${LOCATION:-us-west1}"
SA="${SA:-308662233319-compute@developer.gserviceaccount.com}"

SCOPES="https://www.googleapis.com/auth/devstorage.read_write,\
https://www.googleapis.com/auth/logging.write,\
https://www.googleapis.com/auth/monitoring.write"

if [ -z "$BUCKET" ]; then
  echo "usage: $0 <bucket-name> [project-id]" >&2
  exit 64
fi

# Guard: refuse to stop the machine we are running on.
if curl -s -m 2 -H "Metadata-Flavor: Google" \
     http://metadata.google.internal/computeMetadata/v1/instance/name 2>/dev/null \
     | grep -qx "$VM_NAME"; then
  echo "REFUSING: this is $VM_NAME. Stopping it from here would leave nothing" >&2
  echo "running to start it again. Run this from your laptop or Cloud Shell." >&2
  exit 1
fi

echo "==> creating gs://${BUCKET} (versioned)"
gcloud storage buckets create "gs://${BUCKET}" \
  --project="$PROJECT" --location="$LOCATION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${BUCKET}" --versioning

echo "==> granting objectAdmin on the bucket to ${SA}"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin

echo "==> stopping ${VM_NAME} (the agent is down from here until it restarts)"
gcloud compute instances stop "$VM_NAME" --zone="$ZONE" --project="$PROJECT"

echo "==> setting scopes"
gcloud compute instances set-service-account "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT" \
  --service-account="$SA" --scopes="$SCOPES"

echo "==> starting ${VM_NAME}"
gcloud compute instances start "$VM_NAME" --zone="$ZONE" --project="$PROJECT"

cat <<EOF

Done. The VM is back up. Finish on the VM itself:

  gcloud compute ssh ${VM_NAME} --zone=${ZONE}
  cd ~/robinhood && git pull
  ./scripts/install-gcs-backup.sh ${BUCKET}
  ./scripts/backup-journal-gcs.sh    # verify once by hand

Then confirm the trading timer survived the reboot:
  systemctl list-timers 'robinhood-agent@*' 'rh-token-refresh@*' --no-pager
EOF
