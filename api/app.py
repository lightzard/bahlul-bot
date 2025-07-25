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
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4")  # Default to grok-4
GROK_API_URL = "https://api.x.ai/v1/chat/completions"

async def initialize_application():
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN is not set")  # Temporary
        raise ValueError("TELEGRAM_TOKEN is not set")
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await application.initialize()
    print("Application initialized")  # Temporary
    return application

# Define the message handler to call Grok API
async def handle_message(update, context):
    if not update.message or not update.message.text:
        print("No message or text in update")  # Temporary
        return
    print(f"Processing message: {update.message.text}")  # Temporary
    user_message = update.message.text
    grok_response = await call_grok_api(user_message)
    try:
        await update.message.reply_text(grok_response)
        print(f"Sent response: {grok_response}")  # Temporary
    except Exception as e:
        print(f"Error sending response: {str(e)}")  # Temporary
        return f"Error sending response: {str(e)}"

# Function to call Grok API
async def call_grok_api(message):
    if not GROK_API_KEY:
        print("Error: GROK_API_KEY is not set")  # Temporary
        return "Error: GROK_API_KEY is not set"
    if not GROK_MODEL:
        print("Error: GROK_MODEL is not set")  # Temporary
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
                print("Error: No choices in Grok API response")  # Temporary
                return "Error: No choices in Grok API response"
            return data["choices"][0].get("message", {}).get("content", "No response content")
        except httpx.HTTPStatusError as e:
            error_message = f"HTTP {e.response.status_code}: {e.response.text}"
            print(f"HTTP error: {error_message}")  # Temporary
            return f"Error: {error_message}"
        except httpx.RequestError as e:
            error_message = f"Network error: {type(e).__name__}: {str(e)}"
            print(f"Network error: {error_message}")  # Temporary
            return f"Error: {error_message}"
        except Exception as e:
            error_message = f"Unexpected error: {type(e).__name__}: {str(e)}"
            print(f"Unexpected error: {error_message}")  # Temporary
            return f"Error: {error_message}"

# Webhook endpoint
@app.post("/")
async def telegram_webhook(request: Request):
    try:
        application = await initialize_application()
        update = Update.de_json(await request.json(), application.bot)
        if not update:
            print("Invalid update received")  # Temporary
            return {"ok": False, "error": "Invalid update"}, 400
        await application.process_update(update)
        print("Update processed successfully")  # Temporary
        return {"ok": True}
    except Exception as e:
        print(f"Webhook error: {str(e)}")  # Temporary
        return {"ok": False, "error": str(e)}, 500