# Knowledge base local setup (Obsidian + rclone)

The canonical knowledge store is a `kb/` prefix on your RunPod S3-compatible
Network Volume. Locally, the same files are an Obsidian vault in
`knowledge/`.

## 1. Get the S3 details from RunPod

1. **Endpoint:** RunPod Console → Storage → Network Volume → your volume.
   The S3 endpoint is `https://s3api-<DATACENTER>.runpod.io` where
   `<DATACENTER>` is where your volume lives (shown on the volume page; it
   must be one of the S3-enabled datacenters, e.g. US-CA-2, EU-CZ-1).
2. **Bucket:** the **Network Volume ID** itself (the volume's ID string on
   the same page) — paths are `s3://<volume-id>/kb/...`.
3. **Keys:** Console → **Settings → Credentials**
   (https://console.runpod.io/user/credentials) → **S3 API Keys** tab →
   **Create an S3 API key**. Save the **access key** (`user_...`) and the
   **secret** (`rps_...`) — the secret is shown only once. This key is
   separate from your regular RunPod API key.

These map to the bot's env vars:

```
KNOWLEDGE_S3_ENDPOINT=https://...
KNOWLEDGE_BUCKET=<bucket>
KNOWLEDGE_ACCESS_KEY_ID=...
KNOWLEDGE_SECRET_ACCESS_KEY=...
```

## 2. Install rclone

```powershell
winget install Rclone.Rclone
```

## 3. Sync

```powershell
$env:KNOWLEDGE_S3_ENDPOINT="https://..."; $env:KNOWLEDGE_BUCKET="..."; `
$env:KNOWLEDGE_ACCESS_KEY_ID="..."; $env:KNOWLEDGE_SECRET_ACCESS_KEY="..."

mkdir knowledge
.\scripts\kb-sync.ps1 pull   # S3 -> local
.\scripts\kb-sync.ps1 push   # local -> S3 (bot caches expire within 5 min)
```

(The bash variant `scripts/kb-sync.sh` works the same way.)

## 4. Open as an Obsidian vault

Obsidian → **Open folder as vault** → select `D:\dev\bahlul-bot\knowledge`.
Recommended plugins: none required. Optional: *Linter* for consistent
formatting. Avoid plugins that auto-rename files — entry names are the
retrieval keys (a message mentioning "Carl" injects `Carl.md`).

## Entry file format

```markdown
# Carl

## Facts
- curly hair, ~30 years old
- allergic to shellfish

## Log
- 2026-09-26: loves spicy ramen
```

- `## Facts` is yours — the bot never rewrites it.
- `## Log` is append-only; the bot adds `- YYYY-MM-DD: fact` lines there
  (from `[[KB:...]]` blocks and `/remember`). Pull before push to keep them.

## Conflict rule

Syncs are one-way (`sync` semantics, last writer wins per file). At personal
scale this is fine: the bot only appends under `## Log`, you curate in
Obsidian, and `pull` before `push` is the safe habit.
