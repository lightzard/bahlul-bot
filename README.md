# BahlulBot

BahlulBot is a Telegram bot powered by the DeepSeek API for chat, built with FastAPI and hosted on Vercel. It responds to user messages and commands in private and group chats, leveraging the DeepSeek API (OpenAI-compatible) for intelligent responses. The bot also supports image generation via xAI's Grok image model and image editing/drawing via OpenAI. The bot supports conversation context, maintaining a history of interactions to provide coherent responses.

## Features

- **Command Handling**: Responds to `/start` and `/ask <question>` commands in private and group chats.
- **Text Message Handling**: Processes regular text messages in private chats and group chats (if privacy mode is disabled and the bot is an admin).
- **Conversation Context**: Stores up to 10 messages per chat (private or group, including topic threads) in a Redis database with a 1-hour expiry, enabling contextual responses from the DeepSeek API.
- **Webhook-Based**: Uses FastAPI to handle Telegram webhook updates, optimized for Vercel’s serverless environment.
- **DeepSeek API Integration**: Powered by the official DeepSeek API (default model: `deepseek-v4-flash-0731`) for generating chat responses.
- **xAI Image Generation**: Uses xAI's Grok image model (`grok-2-image`) via the xAI SDK for the `/generate` command.
- **Group Chat Support**: Handles group messages and topic threads (supergroups) when properly configured.

## Requirements

### Dependencies
- `fastapi`: For building the webhook-based API.
- `python-telegram-bot>=20.0`: For interacting with the Telegram Bot API.
- `httpx`: For asynchronous HTTP requests (used internally by SDKs).
- `uvicorn`: For running the FastAPI application.
- `redis`: For storing conversation history in a Redis database.
- `openai`: For chat via the DeepSeek API (OpenAI-compatible) and for OpenAI image commands.
- `aiohttp`: For downloading image files during image editing.
- `xai-sdk`: For xAI Grok image generation (`/generate`).

### Environment Variables
- `TELEGRAM_TOKEN`: Your Telegram bot token from `@BotFather`.
- `DEEPSEEK_API_KEY`: Your DeepSeek API key (see https://platform.deepseek.com for details).
- `DEEPSEEK_MODEL`: The DeepSeek model to use for chat (default: `deepseek-v4-flash-0731`).
- `DEEPSEEK_BASE_URL`: Optional DeepSeek API base URL (default: `https://api.deepseek.com`).
- `GROK_API_KEY`: Your xAI Grok API key, required for `/generate` image generation (see https://x.ai/api for details).
- `OPENAI_API_KEY`: Your OpenAI API key, required for `/draw`, `/gooddraw`, `/edit`, and `/goodedit`.
- `REDIS_URL`: The connection URL for your Redis instance (e.g., `rediss://:<token>@<host>:<port>` from Upstash).

## Setup Instructions

1. **Clone the Repository**
   ```bash
   git clone https://github.com/lightzard/bahlul-bot.git
   cd bahlul-bot
   ```

2. **Install Dependencies**
   Ensure you have Python 3.8+ installed. Install the required packages:
   ```bash
   pip install -r api/requirements.txt
   ```
    The `requirements.txt` should contain:
    ```
    fastapi
    python-telegram-bot>=20.0
    httpx
    uvicorn
    redis
    xai-sdk==1.0.1
    aiohttp
    openai
    ```

3. **Set Up a Redis Instance**
   - Sign up for a free Redis database at https://upstash.com/.
   - Create a new Redis database and copy the `REDIS_URL` (e.g., `rediss://:<token>@<host>:<port>`).
   - This is used for storing conversation history to enable contextual responses.

4. **Configure Environment Variables**
   - In Vercel, go to Dashboard > Project > Settings > Environment Variables.
    - Add:
      - `TELEGRAM_TOKEN`: Your bot token from `@BotFather`.
      - `DEEPSEEK_API_KEY`: Your DeepSeek API key.
      - `DEEPSEEK_MODEL`: Set to `deepseek-v4-flash-0731` (or another valid DeepSeek model; see https://platform.deepseek.com).
      - `DEEPSEEK_BASE_URL`: Optional; defaults to `https://api.deepseek.com`.
      - `GROK_API_KEY`: Your xAI Grok API key (required for `/generate`).
      - `OPENAI_API_KEY`: Your OpenAI API key (required for `/draw`, `/gooddraw`, `/edit`, `/goodedit`).
      - `REDIS_URL`: The Redis connection URL from Upstash.

5. **Deploy to Vercel**
   - Connect your GitHub repository to Vercel.
   - Deploy the `api/app.py` endpoint.
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
   - **Group Chat** (with privacy mode off and bot as admin):
     - Send: `/ask What is AI?`
     - Expected: “AI is…”
     - Send: `hello`
     - Expected: A response using context from previous messages (context preserved via Redis).

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

- **DeepSeek API Issues**:
  - Verify `DEEPSEEK_API_KEY` and `DEEPSEEK_MODEL` (see https://platform.deepseek.com).
  - Check logs for errors from DeepSeek interactions (look for `Error processing /ask command` or `Error processing message`).
  - If you see a model-not-found error, confirm `deepseek-v4-flash-0731` is enabled for your account, or override `DEEPSEEK_MODEL` with a valid model ID.
  - Ensure `openai` is installed (`pip show openai`).

- **Grok Image Generation Issues** (`/generate`):
  - Verify `GROK_API_KEY` (see https://x.ai/api).
  - Check logs for errors from xAI SDK interactions.
  - Ensure `xai-sdk` is installed (`pip show xai-sdk` should show version `1.0.1`).

## License

This project is licensed under the MIT License.