from fastapi import FastAPI, Request
import telebot
import httpx
import os
import asyncio
import logging

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4")  # Default to grok-4
GROK_API_URL = "https://api.x.ai/v1/chat/completions"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Webhook URL as environment variable

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize bot
bot = None

async def initialize_bot():
    global bot
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is not set")
        raise ValueError("TELEGRAM_TOKEN is not set")
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL is not set")
        raise ValueError("WEBHOOK_URL is not set")
    bot = telebot.TeleBot(TOKEN)
    
    # Define message handler
    @bot.message_handler(content_types=['text'])
    def handle_message(message):
        logger.info(f"Processing message: {message.text}")
        loop = asyncio.get_event_loop()
        grok_response = loop.run_until_complete(call_grok_api(message.text))
        try:
            bot.reply_to(message, grok_response)
            logger.info(f"Sent response: {grok_response}")
        except Exception as e:
            logger.error(f"Error sending response: {str(e)}")
            return f"Error sending response: {str(e)}"

    logger.info("Bot initialized")
    logger.info(f"Removing existing webhook")
    await bot.remove_webhook()
    logger.info(f"Setting webhook to {WEBHOOK_URL}")
    await bot.set_webhook(url=WEBHOOK_URL)
    logger.info("Webhook set successfully")
    return bot

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
                timeout=9.0
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

# Webhook endpoint
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        global bot
        bot = await initialize_bot()
        update = await request.json()
        bot.process_new_updates([telebot.types.Update.de_json(update)])
        logger.info("Update processed successfully")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return {"ok": False, "error": str(e)}, 500

# Startup event
@app.on_event("startup")
async def startup():
    global bot
    bot = await initialize_bot()