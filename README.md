# BahlulBot

BahlulBot is a Telegram bot powered by the Grok API and OpenAI API, built with FastAPI and designed for deployment on Vercel. It handles user commands and messages in private and group chats, leveraging the Grok API for intelligent text responses and image generation, and the OpenAI API for additional image generation and editing capabilities. The bot supports conversation context using a Redis database and includes image generation and editing features.

## Features

- **Command Handling**:
  - `/start`: Introduces the bot and its capabilities.
  - `/ask <question>`: Answers questions using the Grok API.
  - `/generate <description>`: Generates images using the Grok API.
  - `/draw <description>`: Generates images using the OpenAI API (low quality).
  - `/gooddraw <description>`: Generates high-quality images using the OpenAI API.
  - `/edit <description>`: Edits uploaded images with a prompt using the OpenAI API (low quality).
  - `/goodedit <description>`: Edits uploaded images with a prompt using the OpenAI API (high quality).
- **Text Message Handling**: Processes regular text messages in private chats and group chats (if privacy mode is disabled and the bot is an admin).
- **Conversation Context**: Stores up to 10 messages per chat (private or group, including topic threads) in a Redis database with a 1-hour expiry.
- **Image Generation and Editing**: Supports generating images with `/generate`, `/draw`, and `/gooddraw`, and editing images with `/edit` and `/goodedit`.
- **Webhook-Based**: Uses FastAPI to handle Telegram webhook updates, optimized for Vercel’s serverless environment.
- **Grok API Integration**: Powered by xAI’s Grok API (default model: `grok-3-mini-fast`) for text and image generation.
- **OpenAI API Integration**: Uses OpenAI API for additional image generation and editing features.
- **Group Chat Support**: Handles group messages and topic threads in supergroups when properly configured.
- **Whitelist Access Control**: Restricts bot usage to specified chat or user IDs.

## Project Structure

```
bahlul-bot/
├── api/
│   ├── app.py              # Main FastAPI application and webhook endpoint
│   ├── bot/
│   │   ├── handlers.py     # Command and message handlers
│   │   ├── redis_utils.py  # Redis connection and conversation history management
│   │   ├── logger.py       # Logging configuration
├── requirements.txt         # Python dependencies
├── vercel.json             # Vercel configuration
├── README.md               # Project documentation
├── LICENSE                 # License file
├── .gitignore              # Git ignore rules
```

## Requirements

### Dependencies
- `fastapi`: For building the webhook-based API.
- `python-telegram-bot>=20.0`: For interacting with the Telegram Bot API.
- `httpx`: For asynchronous HTTP requests (used by xAI SDK).
- `uvicorn`: For running the FastAPI application.
- `redis`: For storing conversation history in a Redis database.
- `xai-sdk==1.0.1`: For interacting with the Grok API.
- `aiohttp`: For asynchronous HTTP requests (image downloading).
- `openai`: For interacting with the OpenAI API.

See `requirements.txt` for the full list.

### Environment Variables
- `TELEGRAM_TOKEN`: Telegram bot token from `@BotFather`.
- `GROK_API_KEY`: xAI Grok API key (see https://x.ai/api).
- `OPENAI_API_KEY`: OpenAI API key (see https://platform.openai.com/docs/api-reference).
- `GROK_MODEL`: Grok model to use (default: `grok-3-mini-fast`).
- `REDIS_URL`: Redis connection URL (e.g., `rediss://:<token>@<host>:<port>` from Upstash).
- `WHITELIST_IDS`: Comma-separated list of chat or user IDs allowed to use the bot (optional).

## Setup Instructions

1. **Clone the Repository**
   ```bash
   git clone https://github.com/lightzard/bahlul-bot.git
   cd bahlul-bot
   ```

2. **Install Dependencies**
   Ensure Python 3.8+ is installed. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. **Set Up a Redis Instance**
   - Sign up for a free Redis database at https://upstash.com/.
   - Create a new Redis database and copy the `REDIS_URL` (e.g., `rediss://:<token>@<host>:<port>`).
   - This is used for storing conversation history to enable contextual responses.

4. **Configure Environment Variables**
   - In Vercel, go to Dashboard > Project > Settings > Environment Variables.
   - Add:
     - `TELEGRAM_TOKEN`: Your bot token from `@BotFather`.
     - `GROK_API_KEY`: Your Grok API key.
     - `OPENAI_API_KEY`: Your OpenAI API key.
     - `GROK_MODEL`: Set to `grok-3-mini-fast` (or another valid model).
     - `REDIS_URL`: The Redis connection URL from Upstash.
     - `WHITELIST_IDS`: Optional, e.g., `123456789,-987654321` for allowed chat/user IDs.

5. **Deploy to Vercel**
   - Connect your GitHub repository to Vercel.
   - Deploy the project, ensuring `api/app.py` is the entry point (configured in `vercel.json`).
   - Set the webhook for Telegram:
     ```bash
     curl -X POST "https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook?url=https://<your-vercel-app>.vercel.app/webhook"
     ```

6. **Configure Telegram Bot**
   - In Telegram, chat with `@BotFather`:
     - Create a bot and get the `TELEGRAM_TOKEN`.
     - Disable privacy mode for group chats: `/mybots` > Select your bot > Bot Settings > Group Privacy > Turn off.
   - Add the bot to a group and make it an admin (Settings > Administrators > Add Admin > `@BahlulBot` > Grant “Send Messages”).

7. **Test the Bot**
   - **Private Chat**:
     - Send: `/ask What is the capital of France?`
     - Expected: “The capital of France is Paris.”
     - Send: `What is its population?`
     - Expected: “~2.2 million” (context preserved via Redis).
     - Send: `/generate A cat in a tree`
     - Expected: An image of a cat in a tree.
     - Send: `/draw A cute baby sea otter`
     - Expected: A low-quality image of a sea otter.
     - Send: `/gooddraw A cute baby sea otter`
     - Expected: A high-quality image of a sea otter.
     - Send an image with caption: `/edit Add a hat on the animal`
     - Expected: Edited image with a hat.
     - Send an image with caption: `/goodedit Add a hat on the animal`
     - Expected: High-quality edited image with a hat.
   - **Group Chat** (with privacy mode off and bot as admin):
     - Send: `/ask What is AI?`
     - Expected: “AI is…”
     - Send: `hello`
     - Expected: A contextual response.
     - Send: `/generate A dog on a beach`
     - Expected: An image of a dog on a beach.

8. **Verify Redis Data**
   - Use Upstash Dashboard or CLI:
     ```bash
     upstash redis keys chat:*
     upstash redis get chat:<chat_id>:main
     ```
     - Expected: JSON like `[{"role": "user", "content": "What is the capital of France?"}, {"role": "assistant", "content": "The capital of France is Paris."}, ...]`.
   - Check Vercel logs:
     ```bash
     vercel logs <your-app>.vercel.app
     ```
     - Look for: `Successfully connected to Redis`, `Saved conversation history for chat:...`.

## Troubleshooting

- **Redis Errors**:
  - If logs show `Failed to connect to Redis`, verify `REDIS_URL` in Vercel matches Upstash’s `rediss://` URL.
  - Test Redis locally:
    ```python
    import redis.asyncio as redis
    import asyncio
    async def test_redis():
        client = redis.from_url("<your-rediss-url>", decode_responses=True)
        await client.ping()
        print("Connected")
        await client.close()
    asyncio.run(test_redis())
    ```

- **Group Messages Not Working**:
  - Ensure privacy mode is off via `@BotFather`.
  - Re-add bot as admin in the group.
  - Test with `@BahlulBot hello` and check logs for `Processing message from chat type group`.

- **Conversation Context Not Preserved**:
  - Check logs for `Saved conversation history` or `Retrieved history`.
  - Verify Redis data in Upstash Dashboard.
  - Ensure `REDIS_URL` is correct.

- **Grok API Issues**:
  - Verify `GROK_API_KEY` and `GROK_MODEL` (see https://x.ai/api).
  - Check logs for errors from xAI SDK interactions.
  - Ensure `xai-sdk` is installed (`pip show xai-sdk` should show version `1.0.1`).

- **OpenAI API Issues**:
  - Verify `OPENAI_API_KEY` (see https://platform.openai.com/docs/api-reference).
  - Check logs for errors from OpenAI API interactions.
  - Ensure `openai` is installed (`pip show openai`).

- **Image Generation/Editing Issues**:
  - Ensure prompts are descriptive for `/generate`, `/draw`, `/gooddraw`, `/edit`, and `/goodedit`.
  - Check logs for specific errors during image processing.
  - Verify network connectivity for image downloads (for `/edit` and `/goodedit`).

## License

This project is licensed under the MIT License.