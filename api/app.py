from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import logging
import asyncio
import redis.asyncio as redis
import json
from urllib.parse import urlparse
from xai_sdk import Client
import re
import aiohttp  # For downloading the image file
from openai import AsyncOpenAI  # For OpenAI async client
import base64  # For encoding/decoding image data
import io
from api.web_search import (
    format_search_context,
    needs_web_search,
    search_web,
    WEB_SEARCH_AUTO_ENABLED,
    TAVILY_API_KEY,
)

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
REDIS_URL = os.getenv("REDIS_URL")
WHITELIST_IDS = os.getenv("WHITELIST_IDS", "").split(",") if os.getenv("WHITELIST_IDS") else []

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

telegram_app = None

# Function to check if chat_id or user_id is in whitelist
def is_whitelisted(chat_id: int, user_id: int) -> bool:
    whitelisted = str(chat_id) in WHITELIST_IDS or str(user_id) in WHITELIST_IDS
    logger.info(f"Checking whitelist: chat_id={chat_id}, user_id={user_id}, whitelisted={whitelisted}")
    return whitelisted

# Get a chat response from the DeepSeek API using conversation history
async def get_deepseek_response(conversation: list, web_context: str | None = None) -> str:
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not set")
    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    # Build OpenAI-compatible messages from history
    messages = []
    for msg in conversation:
        if msg["role"] == "system":
            if isinstance(msg["content"], list):
                text = " ".join(part.get("text", "") for part in msg["content"] if isinstance(part, dict))
                messages.append({"role": "system", "content": text})
            else:
                messages.append({"role": "system", "content": msg["content"]})
        else:
            messages.append({"role": msg["role"], "content": msg["content"]})
    # Add output limit instruction without persisting it to history
    messages.append({"role": "system", "content": "Your maximum output is 4096 characters."})
    # Add live web search context when available. This is injected per-request and
    # never persisted to conversation history, preventing stale evidence reuse.
    if web_context:
        messages.append({"role": "system", "content": web_context})
    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages
    )
    return response.choices[0].message.content


# Shared chat-query pipeline with optional live web search
async def process_chat_query(
    update: Update,
    query: str,
    *,
    force_web_search: bool = False,
) -> None:
    """Handle a user query with automatic or forced web-search grounding."""
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id

    logger.info(
        f"Processing query from chat type {chat_type}, chat ID: {chat_id}, "
        f"thread ID: {message_thread_id}, force_web_search={force_web_search}: {query}"
    )

    redis_client = None
    try:
        # Initialize Redis client for this request
        redis_client = await init_redis()
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": query})

        # Decide whether to perform a live web search.
        web_context = None
        if TAVILY_API_KEY and (force_web_search or (WEB_SEARCH_AUTO_ENABLED and needs_web_search(query))):
            logger.info(f"Triggering web search for query: {query}")
            outcome = await search_web(query, redis_client, user_id)
            if outcome.ok:
                web_context = format_search_context(query, outcome.results)
                logger.info(
                    f"Web search returned {len(outcome.results)} results (from_cache={outcome.from_cache})"
                )
            else:
                # Search failed (quota, timeout, provider error). Tell the LLM to
                # clearly disclose that current information could not be verified.
                web_context = (
                    "## Live search context\n"
                    "Live web search was attempted but could not be completed.\n"
                    "Clearly state that you could not verify current information, "
                    "and do not invent facts or dates."
                )
                logger.warning(f"Web search failed, will disclose limitation: {outcome.error}")

        # Call DeepSeek with history and grounding context.
        deepseek_response = await get_deepseek_response(conversation, web_context=web_context)
        logger.info(f"Got response from DeepSeek: {deepseek_response}")

        # Save only user query and assistant answer to history; grounding is ephemeral.
        conversation.append({"role": "assistant", "content": deepseek_response})
        await save_conversation_history(redis_client, conversation_key, conversation)

        # Reply to Telegram
        reply_params = {"text": deepseek_response}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info(f"Sent response to Telegram: {deepseek_response}")
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        reply_params = {"text": f"Error processing your request: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for query")


# Command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start command")
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    await update.message.reply_text("Hello! I'm BahlulBot, powered by DeepSeek. Use /ask <your question> to get a response, /web <your question> to force a live web search, or send a message in private chat.")
    logger.info("Sent /start response")

# Command handler for /ask
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /ask command with no message content")
        return

    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    query = ' '.join(context.args) if context.args else None

    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    logger.info(f"Received /ask command from chat ID: {chat_id}, thread ID: {message_thread_id}, query: {query}")

    if not query:
        reply_params = {"text": "Please provide a question after /ask (e.g., /ask What is the capital of France?)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty query warning")
        return

    # Delegate to the shared chat pipeline (includes automatic web search).
    await process_chat_query(update, query)

# Command handler for /web - force live web search
async def web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /web command with no message content")
        return

    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    query = ' '.join(context.args) if context.args else None

    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    logger.info(f"Received /web command from chat ID: {chat_id}, thread ID: {message_thread_id}, query: {query}")

    if not query:
        reply_params = {"text": "Please provide a question after /web (e.g., /web What is the latest Python version?)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty web query warning")
        return

    if not TAVILY_API_KEY:
        reply_params = {"text": "Live web search is not configured. Please set TAVILY_API_KEY."}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent missing TAVILY_API_KEY warning")
        return

    await process_chat_query(update, query, force_web_search=True)

# Message handler for text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received update with no message content")
        return
    message_text = update.message.text
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id

    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return

    logger.info(f"Received message from chat ID: {chat_id}, thread ID: {message_thread_id}: {message_text}")

    # Delegate to the shared chat pipeline (includes automatic web search).
    await process_chat_query(update, message_text)

# Initialize Redis client
async def init_redis():
    if not REDIS_URL:
        logger.warning("REDIS_URL not set, conversation history will not be stored")
        return None
    
    try:
        # Parse REDIS_URL to validate
        parsed_url = urlparse(REDIS_URL)
        if parsed_url.scheme not in ("redis", "rediss"):
            logger.error(f"Invalid REDIS_URL scheme: {parsed_url.scheme}. Expected redis:// or rediss://")
            return None
        
        # Create Redis client (rediss:// handles TLS automatically)
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        # Test connection
        # await redis_client.ping()
        logger.info("Successfully connected to Redis")
        return redis_client
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {str(e)}")
        return None

# Function to get conversation history from Redis
async def get_conversation_history(redis_client, conversation_key: str) -> list:
    if redis_client is None:
        logger.warning("Redis client not initialized, returning empty history")
        return []
    try:
        history = await redis_client.get(conversation_key)
        if history:
            logger.info(f"Retrieved history for {conversation_key}: {history}")
            return json.loads(history)
        logger.info(f"No history found for {conversation_key}")
        return []
    except Exception as e:
        logger.error(f"Error retrieving conversation history for {conversation_key}: {str(e)}")
        return []

# Function to save conversation history to Redis
async def save_conversation_history(redis_client, conversation_key: str, conversation: list):
    if redis_client is None:
        logger.warning("Redis client not initialized, skipping history save")
        return
    try:
        # Limit history to last 10 messages to avoid token limits
        conversation = conversation[-10:]
        await redis_client.set(conversation_key, json.dumps(conversation))
        # Set expiry to 1 hour to manage storage
        await redis_client.expire(conversation_key, 3600)
        logger.info(f"Saved conversation history for {conversation_key}: {json.dumps(conversation)}")
    except Exception as e:
        logger.error(f"Error saving conversation history for {conversation_key}: {str(e)}")

# Command handler for /generate
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /generate command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    prompt = ' '.join(context.args) if context.args else None
    
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Received /generate command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, prompt: {prompt}")
    
    if not prompt:
        reply_params = {"text": "Please provide a description after /generate (e.g., /generate A cat in a tree)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty prompt warning")
        return
    
    redis_client = None
    try:
        # Initialize Redis client
        redis_client = await init_redis()
        # Initialize xAI SDK client
        xai_client = Client(api_key=GROK_API_KEY, timeout=3600)
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/generate {prompt}"})
        
        # Generate image using xAI SDK
        response = xai_client.image.sample(
            model="grok-2-image",
            prompt=prompt,
            image_format="url"
        )
        image_url = response.url
        revised_prompt = response.prompt
        logger.info(f"Generated image with revised prompt: {revised_prompt}, URL: {image_url}")
        
        # Save to conversation history
        conversation.append({"role": "assistant", "content": f"Generated image: {image_url} (Revised prompt: {revised_prompt})"})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
        # Send image to Telegram
        reply_params = {"photo": image_url}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
        logger.info(f"Sent image to Telegram: {image_url}")
    except Exception as e:
        logger.error(f"Error processing /generate command: {str(e)}")
        reply_params = {"text": f"Error generating image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for /generate")

# Command handler for /draw
async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /draw command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    prompt = ' '.join(context.args) if context.args else None
    
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Received /draw command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, prompt: {prompt}")
    
    if not prompt:
        reply_params = {"text": "Please provide a description after /draw (e.g., /draw A cute baby sea otter)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty prompt warning")
        return
    
    redis_client = None
    try:
        # Initialize Redis client
        redis_client = await init_redis()
        # Initialize OpenAI client
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/draw {prompt}"})
        
        # Generate image using OpenAI
        response = await openai_client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="low",
            moderation="low"
        )
        
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        
        logger.info(f"Generated image with prompt: {prompt}")
        
        # Save to conversation history
        conversation.append({"role": "assistant", "content": f"Generated image with prompt: {prompt}"})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
        # Send image to Telegram
        reply_params = {"photo": image_bytes}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
        logger.info(f"Sent image to Telegram (base64 length: {len(image_base64)})")
    except Exception as e:
        logger.error(f"Error processing /draw command: {str(e)}")
        reply_params = {"text": f"Error generating image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for /draw")

# Command handler for /gooddraw
async def gooddraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /gooddraw command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    prompt = ' '.join(context.args) if context.args else None
    
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Received /gooddraw command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, prompt: {prompt}")
    
    if not prompt:
        reply_params = {"text": "Please provide a description after /gooddraw (e.g., /gooddraw A cute baby sea otter)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty prompt warning")
        return
    
    redis_client = None
    try:
        # Initialize Redis client
        redis_client = await init_redis()
        # Initialize OpenAI client
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/gooddraw {prompt}"})
        
        # Generate image using OpenAI
        response = await openai_client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="auto",
            moderation="low"
        )
        
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        
        logger.info(f"Generated image with prompt: {prompt}")
        
        # Save to conversation history
        conversation.append({"role": "assistant", "content": f"Generated image with prompt: {prompt}"})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
        # Send image to Telegram
        reply_params = {"photo": image_bytes}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
        logger.info(f"Sent image to Telegram (base64 length: {len(image_base64)})")
    except Exception as e:
        logger.error(f"Error processing /gooddraw command: {str(e)}")
        reply_params = {"text": f"Error generating image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for /gooddraw")

# Command handler for /edit
async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global telegram_app
    webhook_info = await telegram_app.bot.get_webhook_info()
    if webhook_info.pending_update_count > 1:
        logger.info(f"Pending updates found: {webhook_info.pending_update_count}. Returning 200 immediately.")
        return
    if update.message is None:
        logger.info("Received /edit command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    caption = update.message.caption
    
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Received /edit command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, caption: {caption}")
    
    # Extract prompt from caption
    prompt_start = len("/edit@BahlulBot") if caption.lower().startswith("/edit@bahlulbot") else len("/edit")
    prompt = caption[prompt_start:].strip()
    photo = update.message.photo[-1]  # Get the highest resolution photo
    
    redis_client = None
    try:
        # Initialize Redis client
        redis_client = await init_redis()
        if redis_client is not None:
            # Attempt to set 'is_editing' to '1' only if it doesn't exist, with 60s expiration
            set_result = await redis_client.set('is_editing', '1', nx=True, ex=60)
            if not set_result:
                logger.info("Another edit is in progress, skipping this request")
                return
        else:
            logger.warning("Redis is not available, proceeding without edit lock")
        
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        
        # Get the photo file
        file = await photo.get_file()
        file_url = file.file_path

        # Download the image
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download image: HTTP {resp.status}")
                image_data = await resp.read()

        image_file = io.BytesIO(image_data)
        image_file.name = "image.png"

        # Make request to OpenAI Image Edit API
        response = await openai_client.images.edit(
            model="gpt-image-1",
            image=image_file,
            prompt=prompt,
            n=1,
            quality='low',
            size='1024x1024'
        )
        
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        
        logger.info("Successfully received response from image edit")
        reply_params = {"photo": image_bytes}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
        logger.info(f"Sent edited image to Telegram (base64 length: {len(image_base64)})")
        
    except Exception as e:
        logger.error(f"Error processing /edit command: {str(e)}")
        reply_params = {"text": f"Error editing image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client is not None:
            await redis_client.delete('is_editing')
            await redis_client.close()
            logger.info("Redis client closed and 'is_editing' key deleted")

# Command handler for /goodedit
async def goodedit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global telegram_app
    webhook_info = await telegram_app.bot.get_webhook_info()
    if webhook_info.pending_update_count > 1:
        logger.info(f"Pending updates found: {webhook_info.pending_update_count}. Returning 200 immediately.")
        return
    if update.message is None:
        logger.info("Received /edit command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    caption = update.message.caption
    
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Received /goodedit command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, caption: {caption}")
    
    # Extract prompt from caption
    prompt_start = len("/goodedit@BahlulBot") if caption.lower().startswith("/goodedit@bahlulbot") else len("/goodedit")
    prompt = caption[prompt_start:].strip()
    photo = update.message.photo[-1]  # Get the highest resolution photo
    
    redis_client = None
    try:
        # Initialize Redis client
        redis_client = await init_redis()
        if redis_client is not None:
            # Attempt to set 'is_editing' to '1' only if it doesn't exist, with 60s expiration
            set_result = await redis_client.set('is_editing', '1', nx=True, ex=60)
            if not set_result:
                logger.info("Another edit is in progress, skipping this request")
                return
        else:
            logger.warning("Redis is not available, proceeding without edit lock")
        
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        
        # Get the photo file
        file = await photo.get_file()
        file_url = file.file_path

        # Download the image
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download image: HTTP {resp.status}")
                image_data = await resp.read()

        image_file = io.BytesIO(image_data)
        image_file.name = "image.png"

        # Make request to OpenAI Image Edit API
        response = await openai_client.images.edit(
            model="gpt-image-1",
            image=image_file,
            prompt=prompt,
            n=1,
            quality='auto',
            size='1024x1024',
            input_fidelity='high'
        )
        
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        
        logger.info("Successfully received response from image edit")
        reply_params = {"photo": image_bytes}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
        logger.info(f"Sent edited image to Telegram (base64 length: {len(image_base64)})")
        
    except Exception as e:
        logger.error(f"Error processing /edit command: {str(e)}")
        reply_params = {"text": f"Error editing image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client is not None:
            await redis_client.delete('is_editing')
            await redis_client.close()
            logger.info("Redis client closed and 'is_editing' key deleted")

# Initialize bot for each request
async def initialize_bot():
    global telegram_app
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is not set")
        raise ValueError("TELEGRAM_TOKEN is not set")
    
    telegram_app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )
    
    # Initialize the application
    logger.info("Initializing Telegram application")
    await telegram_app.initialize()
    
    # Add handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("ask", ask))
    telegram_app.add_handler(CommandHandler("web", web))
    telegram_app.add_handler(CommandHandler("generate", generate))
    telegram_app.add_handler(CommandHandler("draw", draw))
    telegram_app.add_handler(CommandHandler("gooddraw", gooddraw))
    telegram_app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(re.compile(r'^/goodedit(@BahlulBot)?\b.*', re.IGNORECASE)) & ~filters.VIA_BOT,goodedit))
    telegram_app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(re.compile(r'^/edit(@BahlulBot)?\b.*', re.IGNORECASE)) & ~filters.VIA_BOT,edit))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot handlers added")
    return telegram_app

# Webhook endpoint
@app.post("/webhook")
async def telegram_webhook(request: Request):
    global telegram_app
    try:
        telegram_app = await initialize_bot()
        update_json = await request.json()
        logger.info(f"Received update: {update_json}")
        update = Update.de_json(update_json, telegram_app.bot)

        # Process updates synchronously
        await telegram_app.process_update(update)
        logger.info("Update processed successfully")
        await telegram_app.shutdown()
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        await telegram_app.shutdown()
        return Response(content=f"Error: {str(e)}", status_code=500)

# Startup event
@app.on_event("startup")
async def startup():
    logger.info("Application startup")

# Shutdown event
@app.on_event("shutdown")
async def shutdown():
    logger.info("Application shutdown")