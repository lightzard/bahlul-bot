"""Shared Markdown knowledge base on a RunPod S3 Network Volume.

One .md file per entry (kb/Carl.md, kb/Johnson.md) in an S3-compatible
bucket. The bot reads entries to ground /ask and /nsfw replies and appends
new facts captured from [[KB:Name]]...[[/KB]] marker blocks; locally the
same files are an Obsidian vault synced with scripts/kb-sync.* (rclone).
Writes never touch the third-party content pipeline — storage and compute
stay on the user's own RunPod/Vercel infrastructure.

Follows the web_search conventions: Redis-cached reads with a short TTL,
context injected as an ephemeral system message that is never persisted to
conversation history, and graceful failure (chat never breaks because the
knowledge base is unreachable).

Entry file format:
    # Carl
    ## Facts
    - curated facts (only the owner edits these, in Obsidian)
    ## Log
    - 2026-09-26: facts the bot captured during chats (append-only)
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from api import settings

logger = logging.getLogger(__name__)

KB_OBJECT_PREFIX = "kb/"
KB_INDEX_CACHE_KEY = "kb:index"


class KnowledgeBaseError(Exception):
    """Raised when the knowledge backend fails or is misconfigured."""


def is_configured() -> bool:
    """The feature is on only when all four KNOWLEDGE_* secrets are set."""
    return all(
        (
            settings.KNOWLEDGE_S3_ENDPOINT,
            settings.KNOWLEDGE_BUCKET,
            settings.KNOWLEDGE_ACCESS_KEY_ID,
            settings.KNOWLEDGE_SECRET_ACCESS_KEY,
        )
    )


_s3_client = None


def _get_s3():
    global _s3_client
    if not is_configured():
        raise KnowledgeBaseError(
            "Knowledge base is not configured: set KNOWLEDGE_S3_ENDPOINT, "
            "KNOWLEDGE_BUCKET, KNOWLEDGE_ACCESS_KEY_ID, and "
            "KNOWLEDGE_SECRET_ACCESS_KEY."
        )
    if _s3_client is None:
        import boto3
        from botocore.config import Config

        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.KNOWLEDGE_S3_ENDPOINT,
            aws_access_key_id=settings.KNOWLEDGE_ACCESS_KEY_ID,
            aws_secret_access_key=settings.KNOWLEDGE_SECRET_ACCESS_KEY,
            # Signature v4 is what MinIO/CEPH-based endpoints (RunPod) expect.
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
    return _s3_client


# ---------------------------------------------------------------------------
# S3 operations (sync bodies, run via asyncio.to_thread)
# ---------------------------------------------------------------------------
def _list_names_sync() -> list[str]:
    s3 = _get_s3()
    resp = s3.list_objects_v2(Bucket=settings.KNOWLEDGE_BUCKET, Prefix=KB_OBJECT_PREFIX)
    names = []
    for obj in resp.get("Contents", []):
        key = obj.get("Key", "")
        if key.endswith(".md") and len(key) > len(KB_OBJECT_PREFIX) + 3:
            names.append(key[len(KB_OBJECT_PREFIX) : -3])
    return sorted(names)


def _get_object_sync(name: str) -> str | None:
    s3 = _get_s3()
    try:
        resp = s3.get_object(
            Bucket=settings.KNOWLEDGE_BUCKET, Key=KB_OBJECT_PREFIX + name + ".md"
        )
        return resp["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return None
    except KnowledgeBaseError:
        raise
    except Exception as e:
        # S3 clones report missing keys differently (404 ClientError etc.)
        code = getattr(getattr(e, "response", {}).get("Error", {}), "get", lambda k: "")("Code")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise


def _put_object_sync(name: str, content: str) -> None:
    s3 = _get_s3()
    s3.put_object(
        Bucket=settings.KNOWLEDGE_BUCKET,
        Key=KB_OBJECT_PREFIX + name + ".md",
        Body=content.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Redis-cached reads
# ---------------------------------------------------------------------------
def _file_cache_key(name: str) -> str:
    return f"kb:file:{name.lower()}"


async def list_entries(redis_client) -> list[str]:
    """Sorted entry names, cached briefly so chats don't hit S3 every turn."""
    if redis_client is not None:
        try:
            cached = await redis_client.get(KB_INDEX_CACHE_KEY)
            if cached is not None:
                return json.loads(cached)
        except Exception as e:
            logger.error("Knowledge index cache read failed: %s", e)
    names = await asyncio.to_thread(_list_names_sync)
    if redis_client is not None:
        try:
            await redis_client.set(
                KB_INDEX_CACHE_KEY,
                json.dumps(names),
                ex=settings.KNOWLEDGE_CACHE_TTL_SECONDS,
            )
        except Exception as e:
            logger.error("Knowledge index cache write failed: %s", e)
    return names


async def get_entry(redis_client, name: str) -> str | None:
    """Entry content (or None when it does not exist), Redis-cached."""
    cache_key = _file_cache_key(name)
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as e:
            logger.error("Knowledge file cache read failed: %s", e)
    content = await asyncio.to_thread(_get_object_sync, name)
    if redis_client is not None:
        try:
            await redis_client.set(
                cache_key,
                json.dumps(content),
                ex=settings.KNOWLEDGE_CACHE_TTL_SECONDS,
            )
        except Exception as e:
            logger.error("Knowledge file cache write failed: %s", e)
    return content


def _invalidate_caches(redis_client, name: str) -> None:
    if redis_client is None:
        return

    async def _delete():
        try:
            await redis_client.delete(_file_cache_key(name))
            await redis_client.delete(KB_INDEX_CACHE_KEY)
        except Exception as e:
            logger.error("Knowledge cache invalidation failed: %s", e)

    return _delete()


# ---------------------------------------------------------------------------
# Retrieval (lexical, whole-word) and prompt formatting
# ---------------------------------------------------------------------------
def select_entries(query: str, names: list[str]) -> list[str]:
    """Entries whose name appears as a whole word in the query (the index is
    always injected too, so the model can ask about others by name)."""
    selected = []
    for name in names:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", query, re.IGNORECASE):
            selected.append(name)
            if len(selected) >= settings.KNOWLEDGE_MAX_ENTRIES_INJECTED:
                break
    return selected


def format_knowledge_context(
    names: list[str], entries: dict[str, str], max_chars: int
) -> str:
    """Evidence-style block: the full index plus matched entry contents."""
    index_line = ", ".join(names) if names else "(empty — nothing stored yet)"
    lines = [
        "## Knowledge base",
        f"Known entries: {index_line}",
        "",
        "Instructions:",
        "- The entries below are the user's persistent shared notes; treat "
        "them as established background for this conversation.",
        "- To store a NEW fact the user explicitly stated or confirmed, end "
        "your reply with a block like:",
        "  [[KB:Carl]]",
        "  - one short fact per line",
        "  [[/KB]]",
        f"- At most {settings.KB_MAX_BULLETS_PER_REPLY} bullet lines per "
        "block; such blocks are recorded and hidden from the user, so the "
        "visible reply must be complete without them.",
        "- Only use an existing entry name in [[KB:...]]; for brand-new "
        "subjects pick a short entry name like the person's first name.",
        "- Never invent facts, and never put secrets or speculation into KB "
        "blocks.",
    ]
    budget = max_chars - sum(len(line) + 1 for line in lines) - 64
    for name, content in entries.items():
        block = f"\n[{name}.md]\n{content.strip()}\n"
        if budget <= 0:
            lines.append(f"[{name}.md omitted to keep context compact.]")
            continue
        if len(block) > budget:
            block = block[:budget] + "\n…[truncated]\n"
        lines.append(block)
        budget -= len(block)
    return "\n".join(lines).strip()


async def build_knowledge_context(redis_client, query: str, max_chars: int) -> str | None:
    """One-call helper for the chat flows: select and format, or None when
    nothing matches / the backend is off. Never raises."""
    if not is_configured():
        return None
    try:
        names = await list_entries(redis_client)
        if not names:
            return None
        selected = select_entries(query, names)
        entries = {}
        for name in selected:
            content = await get_entry(redis_client, name)
            if content:
                entries[name] = content
        if not entries:
            return None
        return format_knowledge_context(names, entries, max_chars)
    except Exception as e:
        logger.error("Knowledge context unavailable: %s", e)
        return None


# ---------------------------------------------------------------------------
# Marker parsing and update application
# ---------------------------------------------------------------------------
_KB_BLOCK_RE = re.compile(
    r"\[\[KB:([A-Za-z0-9_\- ]{1,64})\]\]\s*(.*?)\s*\[\[/KB\]\]", re.DOTALL
)


def parse_kb_blocks(reply: str) -> tuple[str, dict[str, list[str]]]:
    """Split [[KB:Name]]...[[/KB]] blocks out of a reply.

    Returns (clean_reply, {name: [bullet, ...]}). Malformed leftovers (an
    opening marker without its closer, or blocks without bullet lines) are
    removed from the visible text and logged, never shown to the user.
    """
    updates: dict[str, list[str]] = {}
    max_bullets = settings.KB_MAX_BULLETS_PER_REPLY

    def _capture(match: re.Match) -> str:
        name = match.group(1).strip()
        body = match.group(2) or ""
        bullets = [
            line.lstrip(" -*•").strip()
            for line in body.splitlines()
            if line.strip().startswith(("-", "*", "•"))
        ]
        bullets = [b for b in bullets if b][:max_bullets]
        if bullets:
            updates.setdefault(name, []).extend(bullets)
        else:
            logger.warning("Discarding empty [[KB:%s]] block", name)
        return ""

    clean = _KB_BLOCK_RE.sub(_capture, reply)
    # An unclosed opening marker would leak raw markup to the user: drop the
    # marker line itself but keep any other content.
    if "[[KB:" in clean or "[[/KB]]" in clean:
        clean = re.sub(r"^\s*\[\[KB:[^\]]*\]\]\s*$", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"^\s*\[\[/KB\]\]\s*$", "", clean, flags=re.MULTILINE)
        logger.warning("Removed malformed KB marker remnants from a reply")
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, updates


def append_to_log(content: str | None, bullets: list[str], name: str) -> str:
    """Return the entry content with bullets appended under '## Log'.

    Pure string function: creates the file body when missing and adds the
    '## Facts'/'## Log' skeleton as needed; existing lines are never
    rewritten. Duplicate bullets (same normalized text) are skipped.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if content is None:
        content = f"# {name}\n\n## Facts\n\n## Log\n"
    lines = content.rstrip("\n").split("\n")

    try:
        log_idx = next(i for i, line in enumerate(lines) if line.strip() == "## Log")
    except StopIteration:
        lines.extend(["", "## Log"])
        log_idx = len(lines) - 1

    # '## Log' runs until the next heading of the same level or EOF.
    end_idx = len(lines)
    for i in range(log_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            end_idx = i
            break

    section = "\n".join(lines[log_idx + 1 : end_idx]).lower()
    new_lines = []
    for bullet in bullets:
        if bullet.lower() not in section:
            new_lines.append(f"- {today}: {bullet}")
            section += f"\n{bullet.lower()}"

    if not new_lines:
        return content
    return "\n".join(lines[:end_idx] + new_lines + [""] + lines[end_idx:]).rstrip("\n") + "\n"


async def apply_kb_updates(redis_client, updates: dict[str, list[str]]) -> int:
    """Read-modify-write each mentioned entry; returns entries updated.

    Failures are logged per entry and never propagate — a broken knowledge
    backend must not fail the chat that produced the fact.
    """
    applied = 0
    for name, bullets in updates.items():
        if not bullets:
            continue
        try:
            current = await asyncio.to_thread(_get_object_sync, name)
            updated = append_to_log(current, bullets, name)
            await asyncio.to_thread(_put_object_sync, name, updated)
            invalidation = _invalidate_caches(redis_client, name)
            if invalidation is not None:
                await invalidation
            applied += 1
            logger.info("Knowledge entry '%s' updated with %d bullet(s)", name, len(bullets))
        except Exception as e:
            logger.error("Failed to update knowledge entry '%s': %s", name, e)
    return applied
