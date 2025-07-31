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
from xai_sdk.chat import user, system, assistant
from xai_sdk.search import SearchParameters
import re
import aiohttp  # For downloading the image file
from openai import AsyncOpenAI  # For OpenAI async client
import base64  # For encoding/decoding image data
import io

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Added for OpenAI API
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3-mini-fast")
REDIS_URL = os.getenv("REDIS_URL")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start command")
    await update.message.reply_text("Hello! I'm BahlulBot, powered by Grok. Use /ask <your question> to get a response, or send a message in private chat.")
    logger.info("Sent /start response")

# Command handler for /ask
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /ask command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    message_thread_id = update.message.message_thread_id
    query = ' '.join(context.args) if context.args else None
    
    logger.info(f"Received /ask command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, query: {query}")
    
    if not query:
        reply_params = {"text": "Please provide a question after /ask (e.g., /ask What is the capital of France?)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty query warning")
        return
    
    redis_client = None
    try:
        # Initialize Redis client for this request
        redis_client = await init_redis()
        # Initialize xAI SDK client
        xai_client = Client(api_key=GROK_API_KEY, timeout=3600)
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": query})
        conversation.append({"role": "system", "content": [{"type": "text","text": "Your maximum output is 4096 characters."}]})

        # Create chat session with search parameters
        chat = xai_client.chat.create(
            model=GROK_MODEL,
            search_parameters=SearchParameters(mode="auto")
        )
        for msg in conversation:
            if msg["role"] == "user":
                chat.append(user(msg["content"]))
            elif msg["role"] == "system":
                chat.append(system(msg["content"][0]["text"]))
            elif msg["role"] == "assistant":
                chat.append(assistant(msg["content"]))

        # Call Grok API with history
        response = chat.sample()
        grok_response = response.content
        conversation.pop()
        logger.info(f"Got response from Grok: {grok_response}")
        
        # Save to conversation history
        conversation.append({"role": "assistant", "content": grok_response})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
        # Reply to Telegram
        reply_params = {"text": grok_response}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info(f"Sent response to Telegram: {grok_response}")
    except Exception as e:
        logger.error(f"Error processing /ask command: {str(e)}")
        reply_params = {"text": f"Error processing your request: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for /ask")

# Message handler for text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received update with no message content")
        return
    message_text = update.message.text
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    message_thread_id = update.message.message_thread_id
    logger.info(f"Processing message from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}: {message_text}")
    
    redis_client = None
    try:
        # Initialize Redis client for this request
        redis_client = await init_redis()
        # Initialize xAI SDK client
        xai_client = Client(api_key=GROK_API_KEY, timeout=3600)
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": message_text})
        conversation.append({"role": "system", "content": [{"type": "text","text": "Your maximum output is 4096 characters."}]})

        # Create chat session with search parameters
        chat = xai_client.chat.create(
            model=GROK_MODEL,
            search_parameters=SearchParameters(mode="auto")
        )
        for msg in conversation:
            if msg["role"] == "user":
                chat.append(user(msg["content"]))
            elif msg["role"] == "system":
                chat.append(system(msg["content"][0]["text"]))
            elif msg["role"] == "assistant":
                chat.append(assistant(msg["content"]))

        # Call Grok API with history
        response = chat.sample()
        grok_response = response.content
        conversation.pop()
        logger.info(f"Got response from Grok: {grok_response}")
        
        # Save to conversation history
        conversation.append({"role": "assistant", "content": grok_response})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
        # Reply to Telegram
        reply_params = {"text": grok_response}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info(f"Sent response to Telegram: {grok_response}")
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        reply_params = {"text": f"Error processing your request: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for handle_message")

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
        await redis_client.ping()
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
    message_thread_id = update.message.message_thread_id
    prompt = ' '.join(context.args) if context.args else None
    
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

# Command handler for /edit
async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /edit command with no message content")
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    message_thread_id = update.message.message_thread_id
    caption = update.message.caption
    
    logger.info(f"Received /edit command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, caption: {caption}")
    
    # Check if caption starts with /edit or /edit@BahlulBot
    if not caption or not re.match(r'^/edit(@BahlulBot)?\b', caption, re.IGNORECASE):
        reply_params = {"text": "Please provide a caption starting with /edit or /edit@BahlulBot followed by the edit instruction (e.g., /edit Change the background to a beach)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent invalid caption warning")
        return
    
    # Extract prompt from caption
    prompt_start = len("/edit@BahlulBot") if caption.lower().startswith("/edit@bahlulbot") else len("/edit")
    prompt = caption[prompt_start:].strip()
    if not prompt:
        reply_params = {"text": "Please provide an edit instruction after /edit or /edit@BahlulBot in the caption (e.g., /edit Change the background to a beach)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent empty prompt warning")
        return

    # Check for photos in the message
    if not update.message.photo:
        reply_params = {"text": "Please attach at least one photo with the /edit or /edit@BahlulBot caption to edit."}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent missing photo warning")
        return
    
    redis_client = None
    try:
        redis_client = await init_redis()
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/edit {prompt}"})

        # Process multiple photos
        images = []
        for photo in update.message.photo:
            file = await photo.get_file()
            file_url = file.file_path
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to download image: HTTP {resp.status}")
                    image_data = await resp.read()
                    image_file = io.BytesIO(image_data)
                    image_file.name = f"image_{photo.file_id}.png"
                    images.append(image_file)

        # Make request to OpenAI Image Edit API
        # Note: OpenAI's edit API typically processes one image, but we'll use the first one for compatibility
        response = await openai_client.images.edit(
            model="gpt-image-1",
            image=images[0],  # Use the first image, as per OpenAI's API
            prompt=prompt
        )
        
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        
        logger.info("Success get response from image edit")
        reply_params = {"photo": image_bytes}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
        logger.info(f"Sent edited image to Telegram (base64 length: {len(image_base64)})")
        
        conversation.append({"role": "assistant", "content": f"Edited image generated with prompt: {prompt}"})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
    except Exception as e:
        logger.error(f"Error processing /edit command: {str(e)}")
        reply_params = {"text": f"Error editing image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info("Sent error message to Telegram")
    finally:
        if redis_client:
            await redis_client.close()
            logger.info("Redis client closed for /edit")

# Initialize bot for each request
async def initialize_bot():
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
    telegram_app.add_handler(CommandHandler("generate", generate))
    telegram_app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(re.compile(r'^/edit(@BahlulBot)?\b.*', re.IGNORECASE)), edit))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot handlers added")
    return telegram_app

# Webhook endpoint
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        telegram_app = await initialize_bot()
        update_json = await request.json()
        logger.info(f"Received update: {update_json}")
        update = Update.de_json(update_json, telegram_app.bot)
        await telegram_app.process_update(update)
        logger.info("Update processed successfully")
        await telegram_app.shutdown()
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return Response(content=f"Error: {str(e)}", status_code=500)

# Startup event
@app.on_event("startup")
async def startup():
    logger.info("Application startup")

# Shutdown event
@app.on_event("shutdown")
async def shutdown():
    logger.info("Application shutdown")