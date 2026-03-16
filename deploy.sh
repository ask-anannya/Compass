#!/bin/bash
set -e

echo "Deploying Compass to Cloud Run..."

gcloud run deploy compass \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --timeout 3600 \
  --session-affinity \
  --set-secrets "GOOGLE_API_KEY=GOOGLE_API_KEY:latest" \
  --project compass-490014

echo "Done. Service URL:"
gcloud run services describe compass \
  --region us-central1 \
  --project compass-490014 \
  --format "value(status.url)"
