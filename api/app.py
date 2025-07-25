from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, MessageHandler, filters
import httpx
import os
import asyncio

app = FastAPI()

# Environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
GROK_API_URL = "https://api.x.ai/v1/chat/completions"

# Initialize Application globally
application = None

async def initialize_application():
    global application
    if application is None:
        if not TOKEN:
            raise ValueError("TELEGRAM_TOKEN is not set")
        application = Application.builder().token(TOKEN).build()
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        await application.initialize()

# Call initialization at startup
@app.on_event("startup")
async def startup():
    await initialize_application()

# Define the message handler to call Grok API
async def handle_message(update, context):
    if not update.message or not update.message.text:
        return
    user_message = update.message.text
    grok_response = await call_grok_api(user_message)
    await update.message.reply_text(grok_response)

# Function to call Grok API
async def call_grok_api(message):
    if not GROK_API_KEY:
        return "Error: GROK_API_KEY is not set"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                GROK_API_URL,
                json={
                    "model": "grok-4",  # Adjust if model name differs
                    "messages": [{"role": "user", "content": message}]
                },
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                timeout=3600
            )
            response.raise_for_status()
            data = response.json()
            if "choices" not in data or not data["choices"]:
                return "Error: No choices in Grok API response"
            return data["choices"][0].get("message", {}).get("content", "No response content")
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code}: {e.response.text}"
        except httpx.RequestError as e:
            return f"Error: Network error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

# Webhook endpoint
@app.post("/")
async def telegram_webhook(request: Request):
    try:
        if application is None or not application.initialized:
            await initialize_application()
        update = Update.de_json(await request.json(), application.bot)
        if not update:
            return {"ok": False, "error": "Invalid update"}, 400
        await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

# Shutdown handling
@app.on_event("shutdown")
async def shutdown():
    if application and application.initialized:
        await application.stop()