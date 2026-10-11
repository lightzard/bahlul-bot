#!/usr/bin/env bash
# Sync the shared knowledge base between this folder (your Obsidian vault)
# and the RunPod S3 Network Volume the bot reads/writes.
#
# Usage:
#   ./kb-sync.sh pull   # S3 -> local (bot-written ## Log lines land here)
#   ./kb-sync.sh push   # local -> S3 (your curated edits go live)
#
# Required environment variables (same values as the bot's Vercel env):
#   KNOWLEDGE_S3_ENDPOINT, KNOWLEDGE_BUCKET,
#   KNOWLEDGE_ACCESS_KEY_ID, KNOWLEDGE_SECRET_ACCESS_KEY
#
# Requires rclone on PATH.
set -euo pipefail

for name in KNOWLEDGE_S3_ENDPOINT KNOWLEDGE_BUCKET KNOWLEDGE_ACCESS_KEY_ID KNOWLEDGE_SECRET_ACCESS_KEY; do
  [ -n "${!name:-}" ] || { echo "Missing environment variable: $name" >&2; exit 1; }
done

# One-time remote setup (idempotent). The region is the datacenter from the
# endpoint URL (https://s3api-<datacenter>.runpod.io) — RunPod signs against it.
region=$(printf '%s' "$KNOWLEDGE_S3_ENDPOINT" | sed -n 's/.*s3api-\([a-z0-9-]*\)\.runpod\.io.*/\1/p')
: "${region:=us-east-1}"
rclone config create runpodkb s3 \
  provider Other \
  endpoint "$KNOWLEDGE_S3_ENDPOINT" \
  region "$region" \
  access_key_id "$KNOWLEDGE_ACCESS_KEY_ID" \
  secret_access_key "$KNOWLEDGE_SECRET_ACCESS_KEY" >/dev/null

case "${1:-pull}" in
  pull)
    rclone sync "runpodkb:$KNOWLEDGE_BUCKET/kb" ./knowledge --progress
    echo "Pulled S3 -> ./knowledge"
    ;;
  push)
    rclone sync ./knowledge "runpodkb:$KNOWLEDGE_BUCKET/kb" --progress
    echo "Pushed ./knowledge -> S3 (bot caches expire within 5 minutes)"
    ;;
  *)
    echo "Unknown action '${1}'. Use pull or push." >&2
    exit 1
    ;;
esac
