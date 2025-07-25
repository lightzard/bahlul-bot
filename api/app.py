from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, MessageHandler, filters
import httpx
import os

app = FastAPI()

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROK_API_KEY = os.getenv("GROK_API_KEY")
GROK_API_URL = "https://api.x.ai/v1/chat/completions"

application = Application.builder().token(TOKEN).build()

async def handle_message(update, context):
    user_message = update.message.text
    grok_response = await call_grok_api(user_message)
    await update.message.reply_text(grok_response)

async def call_grok_api(message):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                GROK_API_URL,
                json={
                    "model": "grok-4",
                    "messages": [{"role": "user", "content": message}]
                },
                headers={"Authorization": f"Bearer {GROK_API_KEY}"}
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0].get("message", {}).get("content", "No response content")
        except Exception as e:
            return f"Error: {str(e)}"

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@app.post("/")
async def telegram_webhook(request: Request):
    update = Update.de_json(await request.json(), application.bot)
    await application.process_update(update)
    return {"ok": True}