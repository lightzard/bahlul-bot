# Knowledge base local setup (Obsidian + rclone)

The canonical knowledge store is a `kb/` prefix on your RunPod S3-compatible
Network Volume. Locally, the same files are an Obsidian vault in
`knowledge/`.

## 1. Get the S3 details from RunPod

RunPod Console → **Storage → Network Volume** → your volume → the S3
-compatible endpoint section. Note the **endpoint URL** (form
`https://s3api-<region>.runpod.io` or similar), the **bucket name**, and
create an **access key + secret** scoped to the volume. These are the same
values the bot uses as env vars:

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
