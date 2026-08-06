"""BahlulBot Telegram bot (FastAPI + Vercel serverless webhook)."""

import base64
import io
import json
import logging
import re
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
from xai_sdk import Client

from api import settings
from api.web_search import format_search_context, needs_web_search, search_web

app = FastAPI()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

telegram_app = None

# Matches /edit or /goodedit photo captions, with or without the bot username.
_EDIT_COMMAND_PATTERN = re.compile(
    rf"^/(?P<command>edit|goodedit)(?:@{settings.BOT_USERNAME})?\b.*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _query_from_args(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return " ".join(context.args) if context.args else None


def _conversation_key(chat_id: int, message_thread_id: int | None) -> str:
    return f"chat:{chat_id}:{message_thread_id or 'main'}"


async def _reply(update: Update, text: str | None = None, photo=None) -> None:
    params = {}
    if update.message.message_thread_id:
        params["message_thread_id"] = update.message.message_thread_id
    if photo is None:
        await update.message.reply_text(text=text, **params)
    else:
        await update.message.reply_photo(photo=photo, **params)


def is_whitelisted(chat_id: int, user_id: int) -> bool:
    return str(chat_id) in settings.WHITELIST_IDS or str(user_id) in settings.WHITELIST_IDS


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
            await redis_client.close()


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
            "response, /web <your question> to force a live web search, or send a message "
            "in private chat."
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
    try:
        parsed_url = urlparse(settings.REDIS_URL)
        if parsed_url.scheme not in ("redis", "rediss"):
            logger.error(
                "Invalid REDIS_URL scheme: %s. Expected redis:// or rediss://",
                parsed_url.scheme,
            )
            return None
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        logger.info("Successfully connected to Redis")
        return redis_client
    except Exception as e:
        logger.error("Failed to connect to Redis: %s", e)
        return None


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
        await redis_client.set(conversation_key, json.dumps(conversation))
        await redis_client.expire(conversation_key, settings.CONVERSATION_TTL_SECONDS)
        logger.info("Saved conversation history for %s", conversation_key)
    except Exception as e:
        logger.error("Error saving conversation history for %s: %s", conversation_key, e)


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/generate - xAI Grok image generation."""
    if update.message is None:
        return
    prompt = _query_from_args(context)
    logger.info(
        "Received /generate command from chat type %s, chat ID: %s, prompt: %s",
        update.message.chat.type,
        update.message.chat.id,
        prompt,
    )
    if not await require_whitelist(update):
        return
    if not prompt:
        await _reply(
            update,
            text="Please provide a description after /generate (e.g., /generate A cat in a tree)",
        )
        return

    redis_client = None
    try:
        redis_client = await init_redis()
        xai_client = Client(api_key=settings.GROK_API_KEY, timeout=settings.GROK_TIMEOUT_SECONDS)
        conversation_key = _conversation_key(
            update.message.chat.id, update.message.message_thread_id
        )
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/generate {prompt}"})

        response = xai_client.image.sample(
            model=settings.GROK_IMAGE_MODEL,
            prompt=prompt,
            image_format=settings.GROK_IMAGE_FORMAT,
        )
        logger.info("Generated image with revised prompt: %s", response.prompt)

        conversation.append(
            {
                "role": "assistant",
                "content": f"Generated image: {response.url} (Revised prompt: {response.prompt})",
            }
        )
        await save_conversation_history(redis_client, conversation_key, conversation)
        await _reply(update, photo=response.url)
    except Exception as e:
        logger.error("Error processing /generate command: %s", e)
        await _reply(update, text=f"Error generating image: {e}")
    finally:
        if redis_client:
            await redis_client.close()


async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shared implementation for /draw (low quality) and /gooddraw (auto quality)."""
    if update.message is None:
        return
    command = (update.message.text or "/draw").split()[0].split("@")[0].lstrip("/").lower()
    prompt = _query_from_args(context)
    quality = (
        settings.OPENAI_IMAGE_HIGH_QUALITY
        if command == "gooddraw"
        else settings.OPENAI_IMAGE_QUALITY
    )
    logger.info(
        "Received /%s command from chat ID: %s, thread ID: %s, prompt: %s",
        command,
        update.message.chat.id,
        update.message.message_thread_id,
        prompt,
    )
    if not await require_whitelist(update):
        return
    if not prompt:
        await _reply(
            update,
            text=f"Please provide a description after /{command} "
            f"(e.g., /{command} A cute baby sea otter)",
        )
        return

    redis_client = None
    try:
        redis_client = await init_redis()
        openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        conversation_key = _conversation_key(
            update.message.chat.id, update.message.message_thread_id
        )
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/{command} {prompt}"})

        response = await openai_client.images.generate(
            model=settings.OPENAI_IMAGE_MODEL,
            prompt=prompt,
            n=1,
            size=settings.OPENAI_IMAGE_SIZE,
            quality=quality,
            moderation=settings.OPENAI_IMAGE_MODERATION,
        )
        image_bytes = base64.b64decode(response.data[0].b64_json)

        conversation.append({"role": "assistant", "content": f"Generated image with prompt: {prompt}"})
        await save_conversation_history(redis_client, conversation_key, conversation)
        await _reply(update, photo=image_bytes)
    except Exception as e:
        logger.error("Error processing /%s command: %s", command, e)
        await _reply(update, text=f"Error generating image: {e}")
    finally:
        if redis_client:
            await redis_client.close()


async def edit_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shared implementation for /edit and /goodedit (photo captions)."""
    global telegram_app
    if update.message is None:
        return
    caption = update.message.caption or ""
    match = _EDIT_COMMAND_PATTERN.match(caption)
    if not match:
        return
    command = match.group("command").lower()

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
    photo = update.message.photo[-1]

    is_good = command == "goodedit"
    quality = settings.OPENAI_EDIT_HIGH_QUALITY if is_good else settings.OPENAI_EDIT_QUALITY

    redis_client = None
    try:
        redis_client = await init_redis()
        if redis_client is not None:
            # Single-flight lock so concurrent edits do not overlap.
            lock_acquired = await redis_client.set(
                "is_editing", "1", nx=True, ex=settings.IMAGE_EDIT_LOCK_TTL_SECONDS
            )
            if not lock_acquired:
                logger.info("Another edit is in progress, skipping this request")
                return
        else:
            logger.warning("Redis is not available, proceeding without edit lock")

        openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        file = await photo.get_file()
        async with aiohttp.ClientSession() as session:
            async with session.get(file.file_path) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to download image: HTTP {resp.status}")
                image_data = await resp.read()

        image_file = io.BytesIO(image_data)
        image_file.name = "image.png"

        edit_kwargs = {
            "model": settings.OPENAI_IMAGE_MODEL,
            "image": image_file,
            "prompt": prompt,
            "n": 1,
            "quality": quality,
            "size": settings.OPENAI_IMAGE_SIZE,
        }
        if is_good:
            edit_kwargs["input_fidelity"] = settings.OPENAI_EDIT_HIGH_FIDELITY
        response = await openai_client.images.edit(**edit_kwargs)

        image_bytes = base64.b64decode(response.data[0].b64_json)
        await _reply(update, photo=image_bytes)
    except Exception as e:
        logger.error("Error processing /%s command: %s", command, e)
        await _reply(update, text=f"Error editing image: {e}")
    finally:
        if redis_client is not None:
            await redis_client.delete("is_editing")
            await redis_client.close()


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
    telegram_app.add_handler(CommandHandler("ask", ask))
    telegram_app.add_handler(CommandHandler("web", web))
    telegram_app.add_handler(CommandHandler("generate", generate))
    telegram_app.add_handler(CommandHandler(["draw", "gooddraw"], generate_image))
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

        await telegram_app.process_update(update)
        logger.info("Update processed successfully")
        return Response(status_code=200)
    except Exception as e:
        logger.error("Webhook error: %s", e)
        return Response(content=f"Error: {e}", status_code=500)
    finally:
        if telegram_app is not None:
            await telegram_app.shutdown()
