from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import httpx
import os
import logging
import asyncio

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3-mini-fast") 
GROK_API_URL = "https://api.x.ai/v1/chat/completions"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start command")
    await update.message.reply_text("Hello! I'm a bot powered by Grok. Send me a message, and I'll respond with Grok's answer.")
    logger.info("Sent /start response")

# Message handler for text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    if message_text[0]=='@' and len(message_text)>11:
        message_text=message_text[11:] #skip @bahlulbot<space>
    logger.info(f"Processing message: {message_text}")
    try:
        grok_response = await call_grok_api(message_text)
        logger.info(f"Got response from Grok: {grok_response}")
        await update.message.reply_text(grok_response)
        logger.info(f"Sent response to Telegram: {grok_response}")
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        await update.message.reply_text(f"Error processing your request: {str(e)}")
        logger.info("Sent error message to Telegram")

# Function to call Grok API
async def call_grok_api(message):
    if not GROK_API_KEY:
        logger.error("GROK_API_KEY is not set")
        return "Error: GROK_API_KEY is not set"
    if not GROK_MODEL:
        logger.error("GROK_MODEL is not set")
        return "Error: GROK_MODEL is not set"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                GROK_API_URL,
                json={
                    "model": GROK_MODEL,
                    "messages": [{"role": "user", "content": message}]
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
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot handlers added")
    return telegram_app

# Webhook endpoint
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        # Initialize a new Application for each request
        telegram_app = await initialize_bot()
        update = Update.de_json(await request.json(), telegram_app.bot)
        await telegram_app.process_update(update)
        logger.info("Update processed successfully")
        
        # Shut down the application to clean up resources
        await telegram_app.shutdown()
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return Response(content=f"Error: {str(e)}", status_code=500)

# Startup event (optional, for logging or other initialization)
@app.on_event("startup")
async def startup():
    logger.info("Application startup")

# Shutdown event (optional, for logging)
@app.on_event("shutdown")
async def shutdown():
    logger.info("Application shutdown")