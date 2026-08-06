# BahlulBot

BahlulBot is a Telegram bot powered by the DeepSeek API for chat, built with FastAPI and hosted on Vercel. It responds to user messages and commands in private and group chats, leveraging the DeepSeek API (OpenAI-compatible) for intelligent responses. The bot also supports image generation via xAI's Grok image model and image editing/drawing via OpenAI. The bot supports conversation context, maintaining a history of interactions to provide coherent responses.

## Features

- **Command Handling**: Responds to `/start` and `/ask <question>` commands in private and group chats.
- **Text Message Handling**: Processes regular text messages in private chats and group chats (if privacy mode is disabled and the bot is an admin).
- **Conversation Context**: Stores up to 10 messages per chat (private or group, including topic threads) in a Redis database with a 1-hour expiry, enabling contextual responses from the DeepSeek API.
- **Webhook-Based**: Uses FastAPI to handle Telegram webhook updates, optimized for Vercel’s serverless environment.
- **DeepSeek API Integration**: Powered by the official DeepSeek API (default model: `deepseek-v4-flash`) for generating chat responses.
- **Live Web Search**: Selectively augments freshness-sensitive questions (news, weather, prices, latest versions, etc.) with Tavily search results, cached in Redis and capped by a per-user daily quota. Use `/web <question>` to force a live search.
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

### Secrets (Environment Variables)
Only secrets are configured through environment variables; all other configuration lives in `api/settings.py`.
- `TELEGRAM_TOKEN`: Your Telegram bot token from `@BotFather`.
- `DEEPSEEK_API_KEY`: Your DeepSeek API key (see https://platform.deepseek.com for details).
- `GROK_API_KEY`: Your xAI Grok API key, required for `/generate` image generation (see https://x.ai/api for details).
- `OPENAI_API_KEY`: Your OpenAI API key, required for `/draw`, `/gooddraw`, `/edit`, and `/goodedit`.
- `TAVILY_API_KEY`: Your Tavily API key, required to enable live web search (see https://www.tavily.com). Optional—chat works without it, but automatic recency search is disabled.
- `REDIS_URL`: The connection URL for your Redis instance (e.g., `rediss://:<token>@<host>:<port>` from Upstash). This is a secret too, so it stays in the environment.

### Configuration (`api/settings.py`)
Non-secret configuration is centralized in [api/settings.py](api/settings.py):
- `WHITELIST_IDS`: Chat or user IDs allowed to use the bot. An empty set locks the bot down, so add your IDs here.
- `DEEPSEEK_MODEL`: The DeepSeek model to use for chat (default: `deepseek-v4-flash`).
- `DEEPSEEK_BASE_URL`: DeepSeek API base URL (default: `https://api.deepseek.com`).
- `CHAT_OUTPUT_LIMIT_CHARS`: Maximum output length instruction sent to DeepSeek (default: `4096`).
- `CONVERSATION_HISTORY_LIMIT`: Messages kept per chat in Redis (default: `10`).
- `CONVERSATION_TTL_SECONDS`: How long conversation history is kept (default: `3600`).
- `WEB_SEARCH_AUTO_ENABLED`: Set to `False` to disable automatic recency-triggered searches (default: `True`). `/web` still works when the key is set.
- `WEB_SEARCH_MAX_RESULTS`: Number of search results to fetch and inject (default: `5`).
- `WEB_SEARCH_CACHE_TTL_SECONDS`: How long search results are cached in Redis (default: `600`).
- `WEB_SEARCH_DAILY_LIMIT`: Maximum uncached live searches per user per UTC day (default: `50`).
- `WEB_SEARCH_TIMEOUT_SECONDS`: Timeout for each Tavily request (default: `8`).
- `WEB_SEARCH_CONTEXT_MAX_CHARS`: Cap for the search context injected into DeepSeek (default: `8000`).
- Image settings: model names, output size, quality, moderation, and the edit lock TTL for `/generate`, `/draw`, `/gooddraw`, `/edit`, and `/goodedit`.
- `BOT_USERNAME`: Used to recognize commands such as `/edit@BahlulBot` (default: `BahlulBot`).## Setup Instructions

1. **Clone the Repository**
   ```bash
   git clone https://github.com/lightzard/bahlul-bot.git
   cd bahlul-bot
   ```

2. **Install Dependencies**
   Ensure you have Python 3.8+ installed. Install the required packages:
   ```bash
   pip install -r requirements.txt
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

4. **Configure Secrets (Environment Variables)**
   - In Vercel, go to Dashboard > Project > Settings > Environment Variables.
   - Add only the secrets:
     - `TELEGRAM_TOKEN`: Your bot token from `@BotFather`.
     - `DEEPSEEK_API_KEY`: Your DeepSeek API key.
     - `GROK_API_KEY`: Your xAI Grok API key (required for `/generate`).
     - `OPENAI_API_KEY`: Your OpenAI API key (required for `/draw`, `/gooddraw`, `/edit`, `/goodedit`).
     - `REDIS_URL`: The Redis connection URL from Upstash.
     - `TAVILY_API_KEY`: (Optional) Your Tavily API key for live web search (https://www.tavily.com). Automatic recency search is disabled if omitted.
   - Edit `api/settings.py` for model names, limits, whitelist IDs, and other non-secret options.

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
     - Send: `/ask What is the latest Python version?`
     - Expected: A current answer with numbered sources like `[1]` and a `Sources:` list (triggers live web search).
     - Send: `/web What is happening in the news today?`
     - Expected: A forced live-search answer regardless of automatic detection.
   - **Group Chat** (with privacy mode off and bot as admin):
     - Send: `/ask What is AI?`
     - Expected: “AI is…”
     - Send: `hello`
     - Expected: A response using context from previous messages (context preserved via Redis).

## Live Web Search (Cost Controls)

Web search is intentionally **selective** to keep costs near zero:

- **Local detection, no classifier**: A lightweight regex heuristic decides if a question needs fresh information. It covers English and Bahasa Indonesia across sports, weather, software, politics, and current-affairs domains, with explicit time markers (today, latest, current, hari ini, tadi malam, terbaru, saat ini, ...) taking priority over stable-knowledge guards. Stable questions like "What is the capital of France?" or "Apa itu inflasi?" make **zero** web-search requests.
- **Basic search only**: Uses Tavily `search_depth: basic` with `include_answer: false` and `include_raw_content: false` to avoid premium token/pricing tiers.
- **Compact context**: Only the top 5 results (title, URL, snippet, date) are injected, capped at `WEB_SEARCH_CONTEXT_MAX_CHARS` (default 8000), limiting DeepSeek input-token cost.
- **Redis caching**: Identical queries within `WEB_SEARCH_CACHE_TTL_SECONDS` (default 600) reuse cached results with **no** extra Tavily call.
- **Per-user daily quota**: `WEB_SEARCH_DAILY_LIMIT` (default 50) blocks unlimited uncached searches. Cached hits do not count against the quota.
- **Graceful fallback**: Missing key, quota exhaustion, timeouts, or provider errors do not break chat. The bot clearly states it could not verify current information.
- **Prompt-injection guard**: Snippets are injected as untrusted *evidence only*; the model is instructed never to follow instructions inside them.

### Expected cost example

At 10,000 messages/month with ~10% freshness-sensitive (1,000 searches), Tavily's free tier (1,000 credits/month) covers the search cost at **$0**. The only added cost is the extra DeepSeek tokens for the compact search context.

### /web command

Use `/web <question>` to force a live web search regardless of automatic detection, e.g.:

```
/web What is the current price of Bitcoin?
```

When search succeeds, answers include inline citations like `[1]` and end with a `Sources:` list of URLs.

## Testing

Run all mocked tests (no API keys or network required):

```bash
python test_chat.py
```

Run a real integration test against the DeepSeek API (requires `DEEPSEEK_API_KEY`):

```bash
python test_chat.py --live
```

Run a real integration test against the Tavily API (requires `TAVILY_API_KEY`, costs 1 credit):

```bash
# Windows (PowerShell)
$env:TAVILY_API_KEY="<your-key>"; python test_chat.py --live-tavily

# WSL / Linux / macOS
TAVILY_API_KEY="<your-key>" python test_chat.py --live-tavily
```

This performs a single basic search for `"latest AI news today"`, prints the top 3 result titles and URLs, and exits with status code 0 on success.

## Verify Redis Data

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

- **Web Search Not Working**:
  - Verify `TAVILY_API_KEY` is set in Vercel environment variables.
  - Check logs for `Triggering web search` and `Web search failed` messages.
  - Test the key locally with `curl -X POST https://api.tavily.com/search -H "Content-Type: application/json" -d '{"api_key":"<KEY>","query":"test","search_depth":"basic"}'`.
- **Web Search Daily Limit Reached**:
  - Logs will show `Web search daily limit reached for user <id>`.
  - Increase `WEB_SEARCH_DAILY_LIMIT` in `api/settings.py` to raise the per-user cap.
  - Cached queries do not count toward the daily limit.
- **DeepSeek API Issues**:
  - Verify `DEEPSEEK_API_KEY` and the `DEEPSEEK_MODEL` value in `api/settings.py` (see https://platform.deepseek.com).
  - Check logs for errors from DeepSeek interactions (look for `Error processing /ask command` or `Error processing message`).
  - If you see a model-not-found error, confirm `deepseek-v4-flash` is enabled for your account, or change `DEEPSEEK_MODEL` in `api/settings.py` to a valid model ID.
  - Ensure `openai` is installed (`pip show openai`).

- **Grok Image Generation Issues** (`/generate`):
  - Verify `GROK_API_KEY` (see https://x.ai/api).
  - Check logs for errors from xAI SDK interactions.
  - Ensure `xai-sdk` is installed (`pip show xai-sdk` should show version `1.0.1`).

## License

This project is licensed under the MIT License.