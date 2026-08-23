#!/usr/bin/env bash
# Create a small GCP Compute Engine VM for running the Robinhood trading
# integration. Run this from any machine with the gcloud CLI authenticated
# (gcloud auth login && gcloud config set project <PROJECT_ID>).
set -euo pipefail

VM_NAME="${VM_NAME:-robinhood-trader}"
ZONE="${ZONE:-us-central1-a}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-small}"
IMAGE_FAMILY="${IMAGE_FAMILY:-debian-12}"
IMAGE_PROJECT="${IMAGE_PROJECT:-debian-cloud}"

gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size=20GB

echo
echo "VM created. SSH in with:"
echo "  gcloud compute ssh $VM_NAME --zone=$ZONE"
echo "Then run scripts/setup-vm.sh on the VM."
