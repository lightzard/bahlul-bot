from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application
import os
import asyncio
from bot.handlers import (
    start, ask, generate, draw, gooddraw, edit, goodedit, handle_message
)
from bot.logger import logger, setup_logging
from bot.redis_utils import init_redis  # Imported but not directly used here; handlers use it

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3-mini-fast")
REDIS_URL = os.getenv("REDIS_URL")
WHITELIST_IDS = os.getenv("WHITELIST_IDS", "").split(",") if os.getenv("WHITELIST_IDS") else []

# Setup logging
setup_logging()

telegram_app = None

# Startup event: Initialize bot once (for potential reuse in serverless)
@app.on_event("startup")
async def startup():
    global telegram_app
    logger.info("Application startup")
    telegram_app = await initialize_bot()

# Shutdown event
@app.on_event("shutdown")
async def shutdown():
    logger.info("Application shutdown")
    if telegram_app:
        await telegram_app.shutdown()

# Initialize bot (called on startup)
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
    
    logger.info("Bot handlers added")
    return telegram_app

# Webhook endpoint
@app.post("/webhook")
async def telegram_webhook(request: Request):
    global telegram_app
    try:
        update_json = await request.json()
        logger.info(f"Received update: {update_json}")
        update = Update.de_json(update_json, telegram_app.bot)

        # Process updates synchronously
        await telegram_app.process_update(update)
        logger.info("Update processed successfully")
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return Response(content=f"Error: {str(e)}", status_code=500)