"""BahlulBot Telegram bot (FastAPI + Vercel serverless webhook)."""

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from api import image_backend, settings, text_backend
from api.image_backend import ImageBackendError
from api.text_backend import TextBackendError
from api.web_search import format_search_context, needs_web_search, search_web

app = FastAPI()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

telegram_app = None

# Matches /edit or /editlora photo captions (or shorthands /e, /el), with or
# without the bot username (longest alternatives first so they aren't truncated).
_EDIT_COMMAND_PATTERN = re.compile(
    rf"^/(?P<command>editlora|edit|el|e)(?:@{settings.BOT_USERNAME})?\b.*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _query_from_args(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return " ".join(context.args) if context.args else None


def _conversation_key(
    chat_id: int, message_thread_id: int | None, namespace: str = "chat"
) -> str:
    return f"{namespace}:{chat_id}:{message_thread_id or 'main'}"


async def _reply(update: Update, text: str | None = None, photo=None) -> None:
    params = {}
    if update.message.message_thread_id:
        params["message_thread_id"] = update.message.message_thread_id
    if photo is None:
        await update.message.reply_text(text=text, **params)
    else:
        await update.message.reply_photo(
            photo=photo, has_spoiler=settings.IMAGE_HAS_SPOILER, **params
        )


def is_whitelisted(chat_id: int, user_id: int) -> bool:
    return str(chat_id) in settings.WHITELIST_IDS or str(user_id) in settings.WHITELIST_IDS


def _split_message(text: str, limit: int) -> list[str]:
    """Split long output into Telegram-sized chunks, preferring line breaks."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


async def require_whitelist(update: Update) -> bool:
    """Reply with an access-denied message and return False when not whitelisted."""
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    if is_whitelisted(chat_id, user_id):
        return True
    logger.info("Unauthorized access attempt: chat_id=%s, user_id=%s", chat_id, user_id)
    await _reply(update, text="Sorry, you are not authorized to use this bot.")
    return False


# ---------------------------------------------------------------------------
# DeepSeek chat
# ---------------------------------------------------------------------------
async def get_deepseek_response(conversation: list, web_context: str | None = None) -> str:
    if not settings.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not set")
    client = AsyncOpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL)

    messages = []
    for msg in conversation:
        if msg["role"] == "system" and isinstance(msg["content"], list):
            text = " ".join(
                part.get("text", "") for part in msg["content"] if isinstance(part, dict)
            )
            messages.append({"role": "system", "content": text})
        else:
            messages.append({"role": msg["role"], "content": msg["content"]})

    # Output limit and live search evidence are injected per request only and
    # never persisted to history, so stale grounding is never reused.
    messages.append(
        {
            "role": "system",
            "content": f"Your maximum output is {settings.CHAT_OUTPUT_LIMIT_CHARS} characters.",
        }
    )
    if web_context:
        messages.append({"role": "system", "content": web_context})

    response = await client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
    )
    return response.choices[0].message.content


async def process_chat_query(
    update: Update,
    query: str,
    *,
    force_web_search: bool = False,
) -> None:
    """Handle a query with optional live web-search grounding."""
    chat_id = update.message.chat.id
    message_thread_id = update.message.message_thread_id
    logger.info(
        "Processing query from chat type %s, chat ID: %s, thread ID: %s, "
        "force_web_search=%s: %s",
        update.message.chat.type,
        chat_id,
        message_thread_id,
        force_web_search,
        query,
    )

    redis_client = None
    try:
        redis_client = await init_redis()
        conversation_key = _conversation_key(chat_id, message_thread_id)
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": query})

        web_context = None
        if settings.TAVILY_API_KEY and (
            force_web_search or (settings.WEB_SEARCH_AUTO_ENABLED and needs_web_search(query))
        ):
            logger.info("Triggering web search for query: %s", query)
            outcome = await search_web(query, redis_client, update.message.from_user.id)
            if outcome.ok:
                web_context = format_search_context(query, outcome.results)
                logger.info(
                    "Web search returned %d results (from_cache=%s)",
                    len(outcome.results),
                    outcome.from_cache,
                )
            else:
                # Tell the LLM to clearly disclose that current information could
                # not be verified instead of inventing facts.
                web_context = (
                    "## Live search context\n"
                    "Live web search was attempted but could not be completed.\n"
                    "Clearly state that you could not verify current information, "
                    "and do not invent facts or dates."
                )
                logger.warning("Web search failed, will disclose limitation: %s", outcome.error)

        deepseek_response = await get_deepseek_response(conversation, web_context=web_context)

        # Save only the user query and assistant answer; grounding stays ephemeral.
        conversation.append({"role": "assistant", "content": deepseek_response})
        await save_conversation_history(redis_client, conversation_key, conversation)

        await _reply(update, text=deepseek_response)
    except Exception as e:
        logger.error("Error processing query: %s", e)
        await _reply(update, text=f"Error processing your request: {e}")
    finally:
        if redis_client:
            await _close_redis(redis_client)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start command")
    if not await require_whitelist(update):
        return
    await _reply(
        update,
        text=(
            "Hello! I'm BahlulBot, powered by DeepSeek. Use /ask <your question> to get a "
            "response, /web <your question> to force a live web search, /draw <description> "
            "or /drawlora <description> to generate an image, or caption a photo with "
            "/edit or /editlora <description> to edit it. /nsfw <prompt> gives uncensored, "
            "unfiltered replies. Shorthands: /a, /d, /dl, /e, /el, /n. "
            "You can also send a message in private chat."
        ),
    )


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    logger.info(
        "Received /ask command from chat ID: %s, thread ID: %s, query: %s",
        update.message.chat.id,
        update.message.message_thread_id,
        _query_from_args(context),
    )
    if not await require_whitelist(update):
        return
    query = _query_from_args(context)
    if not query:
        await _reply(
            update,
            text="Please provide a question after /ask (e.g., /ask What is the capital of France?)",
        )
        return
    await process_chat_query(update, query)


async def web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    logger.info(
        "Received /web command from chat ID: %s, thread ID: %s, query: %s",
        update.message.chat.id,
        update.message.message_thread_id,
        _query_from_args(context),
    )
    if not await require_whitelist(update):
        return
    query = _query_from_args(context)
    if not query:
        await _reply(
            update,
            text="Please provide a question after /web (e.g., /web What is the latest Python version?)",
        )
        return
    if not settings.TAVILY_API_KEY:
        await _reply(
            update,
            text="Live web search is not configured. Please set TAVILY_API_KEY.",
        )
        return
    await process_chat_query(update, query, force_web_search=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    logger.info(
        "Received message from chat ID: %s, thread ID: %s: %s",
        update.message.chat.id,
        update.message.message_thread_id,
        update.message.text,
    )
    if not await require_whitelist(update):
        return
    await process_chat_query(update, update.message.text)


# ---------------------------------------------------------------------------
# Redis conversation history
# ---------------------------------------------------------------------------
async def init_redis():
    if not settings.REDIS_URL:
        logger.warning("REDIS_URL not set, conversation history will not be stored")
        return None
    # Tolerate stray quotes/whitespace from copy-pasted env var values; they
    # otherwise break the scheme check or the connection silently.
    url = settings.REDIS_URL.strip().strip('"').strip("'").strip()
    redis_client = None
    try:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in ("redis", "rediss"):
            logger.error(
                "Invalid REDIS_URL scheme: %s. Expected redis:// or rediss://",
                parsed_url.scheme,
            )
            return None
        # Explicit timeouts keep a dead Redis from hanging the webhook until
        # Telegram gives up and re-delivers the update.
        redis_client = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        # from_url() does not open a connection; ping so the success log and
        # the client we hand out reflect a Redis that actually answers.
        await redis_client.ping()
        logger.info("Successfully connected to Redis")
        return redis_client
    except Exception as e:
        logger.error("Failed to connect to Redis: %s", e)
        if redis_client is not None:
            await _close_redis(redis_client)
        return None


async def _close_redis(redis_client) -> None:
    """Close a client across redis-py versions (aclose() replaced close())."""
    if redis_client is None:
        return
    close = getattr(redis_client, "aclose", None) or redis_client.close
    await close()


async def get_conversation_history(redis_client, conversation_key: str) -> list:
    if redis_client is None:
        return []
    try:
        history = await redis_client.get(conversation_key)
        return json.loads(history) if history else []
    except Exception as e:
        logger.error("Error retrieving conversation history for %s: %s", conversation_key, e)
        return []


async def save_conversation_history(redis_client, conversation_key: str, conversation: list) -> None:
    if redis_client is None:
        return
    try:
        conversation = conversation[-settings.CONVERSATION_HISTORY_LIMIT:]
        # Single SET with TTL: one round trip and the expiry is applied
        # atomically with the write, so history can never linger without TTL.
        await redis_client.set(
            conversation_key,
            json.dumps(conversation),
            ex=settings.CONVERSATION_TTL_SECONDS,
        )
        logger.info("Saved conversation history for %s", conversation_key)
    except Exception as e:
        logger.error("Error saving conversation history for %s: %s", conversation_key, e)


# ---------------------------------------------------------------------------
# Image generation and editing (Qwen Image 2.1 via api/image_backend)
# ---------------------------------------------------------------------------
IMAGE_OP_LOCK_KEY = "image_op_lock"


async def _acquire_image_op_lock(redis_client, update: Update) -> bool:
    """Single-flight lock across all image commands.

    A single GPU worker serves the backend, so overlapping requests (including
    Telegram re-deliveries while a job is running) are rejected instead of
    queued. Returns True when the request may proceed; replies to the user
    and returns False otherwise.
    """
    if redis_client is None:
        logger.warning("Redis is not available, proceeding without image op lock")
        return True
    acquired = await redis_client.set(
        IMAGE_OP_LOCK_KEY, "1", nx=True, ex=settings.IMAGE_OP_LOCK_TTL_SECONDS
    )
    if not acquired:
        logger.info("Another image request is in progress, skipping this request")
        await _reply(
            update,
            text=(
                "I'm still busy with the previous image request "
                "(cold starts can take a few minutes) — please try again shortly."
            ),
        )
        return False
    return True


async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/draw - text-to-image with the uncensored Qwen GGUF stack."""
    await _draw_command(update, context, lora=False)


async def drawlora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/drawlora - text-to-image with the base int8 stack + LoRA."""
    await _draw_command(update, context, lora=True)


async def _draw_command(update: Update, context: ContextTypes.DEFAULT_TYPE, *, lora: bool):
    command = "drawlora" if lora else "draw"
    if update.message is None:
        return
    prompt = _query_from_args(context)
    logger.info(
        "Received /%s command from chat type %s, chat ID: %s, prompt: %s",
        command,
        update.message.chat.type,
        update.message.chat.id,
        prompt,
    )
    if not await require_whitelist(update):
        return
    if not prompt:
        await _reply(
            update,
            text=f"Please provide a description after /{command} (e.g., /{command} A cat in a tree)",
        )
        return

    redis_client = None
    lock_acquired = False
    try:
        redis_client = await init_redis()
        lock_acquired = await _acquire_image_op_lock(redis_client, update)
        if not lock_acquired:
            return

        conversation_key = _conversation_key(
            update.message.chat.id, update.message.message_thread_id
        )
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/{command} {prompt}"})

        image_bytes = await image_backend.generate_image(
            prompt,
            width=settings.QWEN_IMAGE_WIDTH,
            height=settings.QWEN_IMAGE_HEIGHT,
            steps=settings.QWEN_IMAGE_STEPS,
            lora=lora,
        )

        conversation.append({"role": "assistant", "content": f"Generated image with prompt: {prompt}"})
        await save_conversation_history(redis_client, conversation_key, conversation)
        await _reply(update, photo=image_bytes)
    except ImageBackendError as e:
        logger.error("Image backend error for /%s: %s", command, e)
        await _reply(update, text=f"Error generating image: {e}")
    except Exception as e:
        logger.error("Error processing /%s command: %s", command, e)
        await _reply(update, text=f"Error generating image: {e}")
    finally:
        if redis_client is not None:
            if lock_acquired:
                await redis_client.delete(IMAGE_OP_LOCK_KEY)
            await _close_redis(redis_client)


async def edit_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/edit or /editlora <prompt> as a photo caption - image editing."""
    global telegram_app
    if update.message is None:
        return
    caption = update.message.caption or ""
    match = _EDIT_COMMAND_PATTERN.match(caption)
    if not match:
        return
    command = match.group("command").lower()
    lora = command in ("editlora", "el")

    # Preserve the existing guard: skip while Telegram still has queued updates.
    webhook_info = await telegram_app.bot.get_webhook_info()
    if webhook_info.pending_update_count > 1:
        logger.info(
            "Pending updates found: %s. Skipping /%s.",
            webhook_info.pending_update_count,
            command,
        )
        return

    logger.info(
        "Received /%s command from chat ID: %s, thread ID: %s, caption: %s",
        command,
        update.message.chat.id,
        update.message.message_thread_id,
        caption,
    )
    if not await require_whitelist(update):
        return

    # Strip the command (and optional @username) from the caption to get the prompt.
    if caption.lower().startswith(f"/{command}@{settings.BOT_USERNAME.lower()}"):
        prompt = caption[len(f"/{command}@{settings.BOT_USERNAME}"):].strip()
    else:
        prompt = caption[len(f"/{command}"):].strip()
    if not prompt:
        await _reply(
            update,
            text=(
                f"Please add a description after /{command} "
                f"(e.g., attach a photo captioned: /{command} make it night)"
            ),
        )
        return
    photo = update.message.photo[-1]

    redis_client = None
    lock_acquired = False
    try:
        redis_client = await init_redis()
        lock_acquired = await _acquire_image_op_lock(redis_client, update)
        if not lock_acquired:
            return

        file = await photo.get_file()
        async with aiohttp.ClientSession() as session:
            async with session.get(file.file_path) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to download image: HTTP {resp.status}")
                image_data = await resp.read()

        image_bytes = await image_backend.edit_image(
            image_data, prompt, steps=settings.QWEN_EDIT_STEPS, lora=lora
        )
        await _reply(update, photo=image_bytes)
    except ImageBackendError as e:
        logger.error("Image backend error for /%s: %s", command, e)
        await _reply(update, text=f"Error editing image: {e}")
    except Exception as e:
        logger.error("Error processing /%s command: %s", command, e)
        await _reply(update, text=f"Error editing image: {e}")
    finally:
        if redis_client is not None:
            if lock_acquired:
                await redis_client.delete(IMAGE_OP_LOCK_KEY)
            await _close_redis(redis_client)


# ---------------------------------------------------------------------------
# Uncensored text generation (HauhauCS Qwen3.5-4B via api/text_backend)
# ---------------------------------------------------------------------------
NSFW_OP_LOCK_KEY = "nsfw_op_lock"


async def _acquire_nsfw_op_lock(redis_client, update: Update) -> bool:
    """Single-flight lock for /nsfw, mirroring the image lock.

    The text endpoint runs Max Workers 1, so overlapping requests (including
    Telegram re-deliveries while a job is running) are rejected instead of
    queued. Returns True when the request may proceed; replies to the user
    and returns False otherwise.
    """
    if redis_client is None:
        logger.warning("Redis is not available, proceeding without /nsfw lock")
        return True
    acquired = await redis_client.set(
        NSFW_OP_LOCK_KEY, "1", nx=True, ex=settings.NSFW_OP_LOCK_TTL_SECONDS
    )
    if not acquired:
        logger.info("Another /nsfw request is in progress, skipping this request")
        await _reply(
            update,
            text=(
                "I'm still busy with the previous /nsfw request "
                "(a cold start can take half a minute) — please try again shortly."
            ),
        )
        return False
    return True


async def _edit_or_reply(placeholder, update: Update, text: str, params: dict) -> None:
    """Replace the warming-up placeholder, falling back to a fresh reply if
    the placeholder was deleted."""
    if placeholder is not None:
        try:
            await placeholder.edit_text(text=text, **params)
            return
        except Exception:
            logger.warning("Could not edit the /nsfw placeholder, sending a new message")
    await _reply(update, text=text)


async def nsfw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nsfw - uncensored generation via the HauhauCS text backend."""
    if update.message is None:
        return
    logger.info(
        "Received /nsfw command from chat ID: %s, thread ID: %s, query: %s",
        update.message.chat.id,
        update.message.message_thread_id,
        _query_from_args(context),
    )
    if not await require_whitelist(update):
        return
    query = _query_from_args(context)
    if not query:
        await _reply(
            update,
            text="Please provide a prompt after /nsfw (e.g., /nsfw write a story about a space smuggler)",
        )
        return
    await _nsfw_command(update, query)


async def _nsfw_command(update: Update, query: str) -> None:
    """Handle /nsfw with its own Redis history, isolated from DeepSeek chats."""
    params = {}
    if update.message.message_thread_id:
        params["message_thread_id"] = update.message.message_thread_id

    redis_client = None
    lock_acquired = False
    placeholder = None
    try:
        redis_client = await init_redis()
        lock_acquired = await _acquire_nsfw_op_lock(redis_client, update)
        if not lock_acquired:
            return
        placeholder = await update.message.reply_text(
            "Generating… (a cold GPU start can take about 30 seconds)",
            **params,
        )

        # Separate namespace so uncensored chats never mix into the DeepSeek
        # history that /ask and free-text messages use.
        conversation_key = _conversation_key(
            update.message.chat.id, update.message.message_thread_id, namespace="nsfw"
        )
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": query})

        # Output limit is injected per request only and never persisted to
        # history, matching the DeepSeek flow. It goes first: the Qwen3.5
        # chat template rejects system messages that are not at the beginning.
        messages = [
            {
                "role": "system",
                "content": f"Your maximum output is {settings.NSFW_OUTPUT_LIMIT_CHARS} characters.",
            },
            *conversation,
        ]

        answer = await text_backend.generate_text(
            messages,
            max_tokens=settings.NSFW_MAX_TOKENS,
            temperature=settings.NSFW_TEMPERATURE,
        )

        conversation.append({"role": "assistant", "content": answer})
        await save_conversation_history(redis_client, conversation_key, conversation)

        chunks = _split_message(answer, settings.NSFW_OUTPUT_LIMIT_CHARS)
        await placeholder.edit_text(chunks[0], **params)
        for chunk in chunks[1:]:
            await update.message.reply_text(text=chunk, **params)
    except TextBackendError as e:
        logger.error("Text backend error for /nsfw: %s", e)
        await _edit_or_reply(placeholder, update, f"Error generating a response: {e}", params)
    except Exception as e:
        logger.error("Error processing /nsfw command: %s", e)
        await _edit_or_reply(placeholder, update, f"Error generating a response: {e}", params)
    finally:
        if redis_client is not None:
            if lock_acquired:
                await redis_client.delete(NSFW_OP_LOCK_KEY)
            await _close_redis(redis_client)


# ---------------------------------------------------------------------------
# Audit trail (who queried the bot; Redis outlives Vercel's short log retention)
# ---------------------------------------------------------------------------
AUDIT_LOG_KEY = "audit:log"


async def _record_audit(redis_client, update_json: dict) -> None:
    """Append one record per received update. Never raises — audit failures
    must not break update processing."""
    if redis_client is None:
        return
    try:
        message = update_json.get("message") or update_json.get("edited_message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        text = message.get("text") or message.get("caption") or ""
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
            "user_id": sender.get("id"),
            "username": sender.get("username"),
            "name": " ".join(
                part for part in (sender.get("first_name"), sender.get("last_name")) if part
            ),
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type"),
            "chat_title": chat.get("title") or chat.get("username"),
            "text": text[: settings.AUDIT_SNIPPET_CHARS],
        }
        await redis_client.lpush(AUDIT_LOG_KEY, json.dumps(record))
        await redis_client.ltrim(AUDIT_LOG_KEY, 0, settings.AUDIT_LOG_MAX_ENTRIES - 1)
    except Exception as e:
        logger.error("Failed to write audit record: %s", e)


async def audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/audit [n] - show the last n updates seen by the bot (owner only)."""
    if update.message is None:
        return
    logger.info("Received /audit command from user ID: %s", update.message.from_user.id)
    if not settings.OWNER_IDS:
        await _reply(update, text="/audit is not configured. Set OWNER_IDS to enable it.")
        return
    if str(update.message.from_user.id) not in settings.OWNER_IDS:
        logger.warning("Unauthorized /audit attempt by user %s", update.message.from_user.id)
        await _reply(update, text="Sorry, /audit is restricted to the bot owner.")
        return

    count = 10
    if context.args:
        try:
            count = max(1, min(50, int(context.args[0])))
        except ValueError:
            pass

    redis_client = None
    try:
        redis_client = await init_redis()
        if redis_client is None:
            await _reply(update, text="Redis is not available — the audit log is empty.")
            return
        records = await redis_client.lrange(AUDIT_LOG_KEY, 0, count - 1)
        if not records:
            await _reply(update, text="No audit records yet.")
            return
        lines = []
        for raw in records:
            try:
                r = json.loads(raw)
            except (TypeError, ValueError):
                continue
            who = f"@{r['username']}" if r.get("username") else (r.get("name") or "?")
            lines.append(
                f"{r.get('ts', '?')} | {who} (id {r.get('user_id', '?')}) | "
                f"chat {r.get('chat_id', '?')} ({r.get('chat_type', '?')}"
                f"{', ' + r['chat_title'] if r.get('chat_title') else ''}) | {r.get('text', '')}"
            )
        await _reply(update, text="\n".join(lines))
    except Exception as e:
        logger.error("Error reading audit log: %s", e)
        await _reply(update, text=f"Error reading the audit log: {e}")
    finally:
        if redis_client:
            await _close_redis(redis_client)


# ---------------------------------------------------------------------------
# Bot initialization and webhook
# ---------------------------------------------------------------------------
async def initialize_bot():
    global telegram_app
    if not settings.TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN is not set")

    telegram_app = Application.builder().token(settings.TELEGRAM_TOKEN).build()
    await telegram_app.initialize()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler(["ask", "a"], ask))
    telegram_app.add_handler(CommandHandler("web", web))
    telegram_app.add_handler(CommandHandler(["draw", "d"], draw))
    telegram_app.add_handler(CommandHandler(["drawlora", "dl"], drawlora))
    telegram_app.add_handler(CommandHandler(["nsfw", "n"], nsfw))
    telegram_app.add_handler(CommandHandler("audit", audit))
    telegram_app.add_handler(
        MessageHandler(
            filters.PHOTO
            & filters.CaptionRegex(_EDIT_COMMAND_PATTERN)
            & ~filters.VIA_BOT,
            edit_image,
        )
    )
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot handlers added")
    return telegram_app


@app.post("/webhook")
async def telegram_webhook(request: Request):
    global telegram_app
    try:
        telegram_app = await initialize_bot()
        update_json = await request.json()
        logger.info("Received update: %s", update_json)
        update = Update.de_json(update_json, telegram_app.bot)

        # Persist who queried the bot before dispatching: Vercel log retention
        # is too short to answer "who used it last night" after the fact.
        audit_redis = await init_redis()
        try:
            await _record_audit(audit_redis, update_json)
        finally:
            if audit_redis is not None:
                await _close_redis(audit_redis)

        await telegram_app.process_update(update)
        logger.info("Update processed successfully")
        return Response(status_code=200)
    except Exception as e:
        logger.error("Webhook error: %s", e)
        return Response(content=f"Error: {e}", status_code=500)
    finally:
        if telegram_app is not None:
            await telegram_app.shutdown()
