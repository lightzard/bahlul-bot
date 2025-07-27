from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import httpx
import os
import logging
import asyncio
import redis.asyncio as redis
import json
from urllib.parse import urlparse

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3")  # Default to grok-3
GROK_API_URL = "https://api.x.ai/v1/chat/completions"
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
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": query})
        conversation.append({"role": "system", "content": [{"type": "text","text": "Your maximum output is 4096 characters."}]})
        
        # Call Grok API with history
        grok_response = await call_grok_api(conversation)
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
        # Get conversation history
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": message_text})
        conversation.append({"role": "system", "content": [{"type": "text","text": "Your maximum output is 4096 characters."}]})
        
        # Call Grok API with history
        grok_response = await call_grok_api(conversation)
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
        # Parse REDIS_URL to check for rediss:// protocol
        parsed_url = urlparse(REDIS_URL)
        if parsed_url.scheme not in ("redis", "rediss"):
            logger.error(f"Invalid REDIS_URL scheme: {parsed_url.scheme}. Expected redis:// or rediss://")
            return None
        
        # Configure Redis with TLS if rediss://
        redis_kwargs = {"decode_responses": True}
        if parsed_url.scheme == "rediss":
            redis_kwargs["ssl"] = True
        
        redis_client = redis.from_url(REDIS_URL, **redis_kwargs)
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

# Function to call Grok API with conversation history
async def call_grok_api(conversation: list):
    if not GROK_API_KEY:
        logger.error("GROK_API_KEY is not set")
        return "Error: GROK_API_KEY is not set"
    if not GROK_MODEL:
        logger.error("GROK_MODEL is not set")
        return "Error: GROK_MODEL is not set"
    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"Sending Grok API request with conversation: {json.dumps(conversation)}")
            response = await client.post(
                GROK_API_URL,
                json={
                    "model": GROK_MODEL,
                    "messages": conversation
                },
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                timeout=60
            )
            response.raise_for_status()
            data = response.json()
            if "choices" not in data or not data["choices"]:
                logger.error("No choices in Grok API response")
                return "Error: No choices in Grok API response"
            return data["choices"][0].get("message", {}).get("content", "No response content")
        except httpx.HTTPStatusError as e:
            error_message = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"HTTP error: {error_message}")
            return f"Error: {error_message}"
        except httpx.RequestError as e:
            error_message = f"Network error: {type(e).__name__}: {str(e)}"
            logger.error(f"Network error: {error_message}")
            return f"Error: {error_message}"
        except Exception as e:
            error_message = f"Unexpected error: {type(e).__name__}: {str(e)}"
            logger.error(f"Unexpected error: {error_message}")
            return f"Error: {error_message}"

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