from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from xai_sdk import Client
from xai_sdk.chat import user, system, assistant
from xai_sdk.search import SearchParameters
from openai import AsyncOpenAI
import re
import aiohttp
import base64
import io
import json
import os
from bot.logger import logger
from bot.redis_utils import init_redis, get_conversation_history, save_conversation_history

GROK_API_KEY = os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3-mini-fast")
WHITELIST_IDS = os.getenv("WHITELIST_IDS", "").split(",") if os.getenv("WHITELIST_IDS") else []

# Function to check if chat_id or user_id is in whitelist
def is_whitelisted(chat_id: int, user_id: int) -> bool:
    whitelisted = str(chat_id) in WHITELIST_IDS or str(user_id) in WHITELIST_IDS
    logger.info(f"Checking whitelist: chat_id={chat_id}, user_id={user_id}, whitelisted={whitelisted}")
    return whitelisted

# Shared function for processing text queries (used by /ask and handle_message)
async def process_text_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Processing text query from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, query: {query}")
    
    redis_client = None
    try:
        redis_client = await init_redis()
        xai_client = Client(api_key=GROK_API_KEY, timeout=3600)
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": query})
        conversation.append({"role": "system", "content": [{"type": "text", "text": "Your maximum output is 4096 characters."}]})

        chat = xai_client.chat.create(model=GROK_MODEL, search_parameters=SearchParameters(mode="auto"))
        for msg in conversation:
            if msg["role"] == "user":
                chat.append(user(msg["content"]))
            elif msg["role"] == "system":
                chat.append(system(msg["content"][0]["text"]))
            elif msg["role"] == "assistant":
                chat.append(assistant(msg["content"]))

        response = chat.sample()
        grok_response = response.content
        conversation.pop()  # Remove system prompt
        logger.info(f"Got response from Grok: {grok_response}")
        
        conversation.append({"role": "assistant", "content": grok_response})
        await save_conversation_history(redis_client, conversation_key, conversation)
        
        reply_params = {"text": grok_response}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        logger.info(f"Sent response to Telegram: {grok_response}")
    except Exception as e:
        logger.error(f"Error processing text query: {str(e)}")
        reply_params = {"text": f"Error processing your request: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
    finally:
        if redis_client:
            await redis_client.close()

# Command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start command")
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    if not is_whitelisted(chat_id, user_id):
        logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    await update.message.reply_text("Hello! I'm BahlulBot, powered by Grok. Use /ask <your question> to get a response, or send a message in private chat.")
    logger.info("Sent /start response")

# Command handler for /ask
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received /ask command with no message content")
        return
    query = ' '.join(context.args) if context.args else None
    if not query:
        reply_params = {"text": "Please provide a question after /ask (e.g., /ask What is the capital of France?)"}
        if update.message.message_thread_id:
            reply_params["message_thread_id"] = update.message.message_thread_id
        await update.message.reply_text(**reply_params)
        return
    await process_text_query(update, context, query)

# Message handler for text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        logger.info("Received update with no message content")
        return
    message_text = update.message.text
    await process_text_query(update, context, message_text)

# Shared function for image generation (used by /generate, /draw, /gooddraw)
async def process_image_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, mode: str = "grok"):
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    
    if not is_whitelisted(chat_id, user_id):
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    if not prompt:
        reply_params = {"text": f"Please provide a description after /{mode} (e.g., /{mode} A cat in a tree)"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
        return
    
    redis_client = None
    try:
        redis_client = await init_redis()
        conversation_key = f"chat:{chat_id}:{message_thread_id or 'main'}"
        conversation = await get_conversation_history(redis_client, conversation_key)
        conversation.append({"role": "user", "content": f"/{mode} {prompt}"})
        
        if mode == "generate":
            xai_client = Client(api_key=GROK_API_KEY, timeout=3600)
            response = xai_client.image.sample(model="grok-2-image", prompt=prompt, image_format="url")
            image_url = response.url
            revised_prompt = response.prompt
            logger.info(f"Generated image with revised prompt: {revised_prompt}, URL: {image_url}")
            conversation.append({"role": "assistant", "content": f"Generated image: {image_url} (Revised prompt: {revised_prompt})"})
            reply_params = {"photo": image_url}
        else:
            openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            quality = "low" if mode == "draw" else "auto"
            response = await openai_client.images.generate(
                model="gpt-image-1", prompt=prompt, n=1, size="1024x1024", quality=quality, moderation="low"
            )
            image_base64 = response.data[0].b64_json
            image_bytes = base64.b64decode(image_base64)
            logger.info(f"Generated image with prompt: {prompt}")
            conversation.append({"role": "assistant", "content": f"Generated image with prompt: {prompt}"})
            reply_params = {"photo": image_bytes}
        
        await save_conversation_history(redis_client, conversation_key, conversation)
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
    except Exception as e:
        logger.error(f"Error processing /{mode} command: {str(e)}")
        reply_params = {"text": f"Error generating image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
    finally:
        if redis_client:
            await redis_client.close()

# Command handler for /generate
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    prompt = ' '.join(context.args) if context.args else None
    await process_image_generation(update, context, prompt, "generate")

# Command handler for /draw
async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    prompt = ' '.join(context.args) if context.args else None
    await process_image_generation(update, context, prompt, "draw")

# Command handler for /gooddraw
async def gooddraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    prompt = ' '.join(context.args) if context.args else None
    await process_image_generation(update, context, prompt, "gooddraw")

# Shared function for image editing (used by /edit, /goodedit)
async def process_image_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str = "edit"):
    if update.message is None:
        return
    
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    message_thread_id = update.message.message_thread_id
    caption = update.message.caption
    
    if not is_whitelisted(chat_id, user_id):
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    
    logger.info(f"Received /{mode} command from chat type {chat_type}, chat ID: {chat_id}, thread ID: {message_thread_id}, caption: {caption}")
    
    bot_username = "@BahlulBot"
    command_pattern = f"/{mode}{bot_username}" if caption.lower().startswith(f"/{mode}{bot_username.lower()}") else f"/{mode}"
    prompt_start = len(command_pattern)
    prompt = caption[prompt_start:].strip()
    photo = update.message.photo[-1]
    
    redis_client = None
    try:
        redis_client = await init_redis()
        if redis_client:
            set_result = await redis_client.set('is_editing', '1', nx=True, ex=60)
            if not set_result:
                logger.info("Another edit is in progress, skipping this request")
                return
        else:
            logger.warning("Redis is not available, proceeding without edit lock")
        
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        file = await photo.get_file()
        file_url = file.file_path

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download image: HTTP {resp.status}")
                image_data = await resp.read()

        image_file = io.BytesIO(image_data)
        image_file.name = "image.png"

        quality = 'low' if mode == "edit" else 'auto'
        fidelity = 'auto' if mode == "edit" else 'high'
        response = await openai_client.images.edit(
            model="gpt-image-1", image=image_file, prompt=prompt, n=1, quality=quality, size='1024x1024', input_fidelity=fidelity
        )
        
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        
        logger.info("Successfully received response from image edit")
        reply_params = {"photo": image_bytes}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
    except Exception as e:
        logger.error(f"Error processing /{mode} command: {str(e)}")
        reply_params = {"text": f"Error editing image: {str(e)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
    finally:
        if redis_client:
            await redis_client.delete('is_editing')
            await redis_client.close()