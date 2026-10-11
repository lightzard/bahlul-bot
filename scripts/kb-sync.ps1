# Sync the shared knowledge base between this folder (your Obsidian vault)
# and the RunPod S3 Network Volume the bot reads/writes.
#
# Usage:
#   .\kb-sync.ps1 pull   # S3 -> local (bot-written ## Log lines land here)
#   .\kb-sync.ps1 push   # local -> S3 (your curated edits go live)
#
# Required environment variables (same values as the bot's Vercel env):
#   KNOWLEDGE_S3_ENDPOINT, KNOWLEDGE_BUCKET,
#   KNOWLEDGE_ACCESS_KEY_ID, KNOWLEDGE_SECRET_ACCESS_KEY
#
# Requires rclone on PATH (winget install Rclone.Rclone).

param([Parameter(Position = 0)][string]$Action = "pull")

$ErrorActionPreference = "Stop"

foreach ($name in "KNOWLEDGE_S3_ENDPOINT", "KNOWLEDGE_BUCKET", "KNOWLEDGE_ACCESS_KEY_ID", "KNOWLEDGE_SECRET_ACCESS_KEY") {
    if (-not (Get-Item "Env:$name" -ErrorAction SilentlyContinue)) {
        Write-Error "Missing environment variable: $name"
    }
}

# One-time remote setup (idempotent). The region is the datacenter from the
# endpoint URL (https://s3api-<datacenter>.runpod.io) — RunPod signs against it.
$region = if ($env:KNOWLEDGE_S3_ENDPOINT -match "s3api-([a-z0-9-]+)\.runpod\.io") { $Matches[1] } else { "us-east-1" }
rclone config create runpodkb s3 `
    provider Other `
    endpoint $env:KNOWLEDGE_S3_ENDPOINT `
    region $region `
    access_key_id $env:KNOWLEDGE_ACCESS_KEY_ID `
    secret_access_key $env:KNOWLEDGE_SECRET_ACCESS_KEY | Out-Null

switch ($Action) {
    "pull" {
        rclone sync "runpodkb:$($env:KNOWLEDGE_BUCKET)/kb" "./knowledge" --progress
        Write-Host "Pulled S3 -> ./knowledge"
    }
    "push" {
        rclone sync "./knowledge" "runpodkb:$($env:KNOWLEDGE_BUCKET)/kb" --progress
        Write-Host "Pushed ./knowledge -> S3 (bot caches expire within 5 minutes)"
    }
    default {
        Write-Error "Unknown action '$Action'. Use pull or push."
    }
}
