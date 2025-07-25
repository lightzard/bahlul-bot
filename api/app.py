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

# Initialize Application
application = Application.builder().token(TOKEN).build()

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
                headers={"Authorization": f"Bearer {GROK_API_KEY}"}
            )
            response.raise_for_status()
            data = response.json()
            print(f"Grok API response: {data}")  # Debugging
            if "choices" not in data or not data["choices"]:
                return "Error: No choices in Grok API response"
            return data["choices"][0].get("message", {}).get("content", "No response content")
        except Exception as e:
            print(f"Grok API error: {str(e)}")  # Debugging
            return f"Error: {str(e)}"

# Add handler
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Initialize Application at startup
@app.on_event("startup")
async def startup():
    await application.initialize()
    print("Application initialized")  # Debugging

# Webhook endpoint
@app.post("/")
async def telegram_webhook(request: Request):
    try:
        update = Update.de_json(await request.json(), application.bot)
        if update:
            await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        print(f"Webhook error: {str(e)}")  # Debugging
        return {"ok": False, "error": str(e)}, 500

# Shutdown handling (optional, for cleanup)
@app.on_event("shutdown")
async def shutdown():
    await application.stop()
    print("Application stopped")