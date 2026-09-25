# BahlulBot

BahlulBot is a Telegram bot powered by the DeepSeek API for chat, built with FastAPI and hosted on Vercel. It responds to user messages and commands in private and group chats, leveraging the DeepSeek API (OpenAI-compatible) for intelligent responses. Image generation (`/draw`) and image editing (`/edit`) run on **Qwen Image 2.1** (GGUF) through an external RunPod Serverless GPU worker running ComfyUI. Uncensored text generation (`/nsfw`) runs on **HauhauCS Qwen3.5-4B Uncensored** (GGUF) through a second RunPod Serverless worker running llama.cpp. The bot supports conversation context, maintaining a history of interactions to provide coherent responses.

## Architecture

```
Telegram -> FastAPI webhook (api/app.py on Vercel)
         -> api/image_backend (provider abstraction)
         -> RunPod Serverless endpoint (runpod/ in this repo)
         -> ComfyUI + UnetLoaderGGUF
         -> Qwen Image 2.1 GGUF weights (cached from Hugging Face)
         -> image bytes back to Telegram

         -> api/text_backend (provider abstraction)
         -> RunPod Serverless endpoint (runpod-text/ in this repo)
         -> llama.cpp llama-server
         -> HauhauCS Qwen3.5-4B Uncensored GGUF (cached from Hugging Face)
         -> reply text back to Telegram
```

The Vercel app stays lightweight — it never executes the model. Hugging Face
([KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF](https://huggingface.co/KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF)
and [HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive))
is only the storage/source for the weights; each worker downloads them once to
a shared RunPod Network Volume. See [runpod/README.md](runpod/README.md) and
[runpod-text/README.md](runpod-text/README.md) for the full GPU backend
deployment guides.

## Features

- **Command Handling**: Responds to `/start` and `/ask <question>` commands in private and group chats (shorthands: `/a` for `/ask`).
- **Text Message Handling**: Processes regular text messages in private chats and group chats (if privacy mode is disabled and the bot is an admin).
- **Conversation Context**: Stores up to 10 messages per chat (private or group, including topic threads) in a Redis database with a 1-hour expiry, enabling contextual responses from the DeepSeek API.
- **Webhook-Based**: Uses FastAPI to handle Telegram webhook updates, optimized for Vercel’s serverless environment.
- **DeepSeek API Integration**: Powered by the official DeepSeek API (default model: `deepseek-flash`) for generating chat responses.
- **Live Web Search**: Selectively augments freshness-sensitive questions (news, weather, prices, latest versions, etc.) with Tavily search results, cached in Redis and capped by a per-user daily quota. Use `/web <question>` to force a live search.
- **Image Generation & Editing (Qwen Image 2.1)**: `/draw <description>` and photo-captioned `/edit <description>` run the uncensored Qwen GGUF; `/drawlora <description>` and `/editlora <description>` run the base int8 checkpoint plus your LoRA. Shorthands: `/d`, `/dl`, `/e`, `/el`. All execute on a RunPod Serverless GPU worker (scale-to-zero) via the provider abstraction in `api/image_backend/`. Results are sent with a **Telegram spoiler cover** (tap-to-reveal, `IMAGE_HAS_SPOILER`) so explicit images stay blurred in the chat.
- **Uncensored Text (HauhauCS Qwen3.5-4B)**: `/nsfw <prompt>` (shorthand `/n`) generates uncensored, unfiltered replies on a separate RunPod Serverless worker running llama.cpp, via the abstraction in `api/text_backend.py`. History is kept separate from DeepSeek chats (Redis keys `nsfw:*`), and a first message after idle may take ~30s while the GPU worker cold-starts.
- **Group Chat Support**: Handles group messages and topic threads (supergroups) when properly configured.

## Requirements

### Dependencies
- `fastapi`: For building the webhook-based API.
- `python-telegram-bot>=20.0`: For interacting with the Telegram Bot API.
- `httpx`: For asynchronous HTTP requests (used internally by SDKs).
- `uvicorn`: For running the FastAPI application.
- `redis`: For storing conversation history in a Redis database.
- `openai`: For chat via the DeepSeek API (OpenAI-compatible).
- `aiohttp`: For talking to the RunPod image backend and downloading Telegram photos.

### Environment Variables
Secrets and access control are configured through environment variables; all other configuration lives in `api/settings.py`.
- `TELEGRAM_TOKEN`: Your Telegram bot token from `@BotFather`.
- `DEEPSEEK_API_KEY`: Your DeepSeek API key (see https://platform.deepseek.com for details).
- `RUNPOD_API_KEY`: Your RunPod API key, required for `/draw`, `/edit`, and `/nsfw` (see [runpod/README.md](runpod/README.md) and [runpod-text/README.md](runpod-text/README.md) to deploy the backends first).
- `RUNPOD_ENDPOINT_ID`: Your RunPod Serverless endpoint ID for the Qwen Image 2.1 worker.
- `RUNPOD_TEXT_ENDPOINT_ID`: Your RunPod Serverless endpoint ID for the HauhauCS text worker (`/nsfw`).
- `IMAGE_BACKEND`: Optional. `runpod` (default) or `dummy` (offline stub for tests).
- `TEXT_BACKEND`: Optional. `runpod` (default) or `dummy` (offline stub for tests).
- `TAVILY_API_KEY`: Your Tavily API key, required to enable live web search (see https://www.tavily.com). Optional—chat works without it, but automatic recency search is disabled.
- `WHITELIST_IDS`: Comma-separated chat or user IDs allowed to use the bot (e.g., `123456789,987654321`). If unset or empty, nobody can use the bot. Note: a whitelisted **group** chat lets *every member* query the bot, and a whitelisted **user** ID works from any chat they use.
- `OWNER_IDS`: Optional. Comma-separated user IDs allowed to use `/audit` (the bot owner). If unset, `/audit` is disabled for everyone.
- `REDIS_URL`: The connection URL for your Redis instance (e.g., `rediss://:<token>@<host>:<port>` from Upstash). This is a secret too, so it stays in the environment.

### Configuration (`api/settings.py`)
Non-secret configuration is centralized in [api/settings.py](api/settings.py):
- `DEEPSEEK_MODEL`: The DeepSeek model to use for chat (default: `deepseek-flash`).
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
- Image settings (`/draw`, `/edit`): backend timeout/polling, the global image single-flight lock TTL, default width/height/steps for Qwen Image 2.1 (25 steps, 1024×1024, matching the official ComfyUI templates), and `IMAGE_HAS_SPOILER` (default `True`) — sends generated/edited photos with a Telegram spoiler cover.
- Text settings (`/nsfw`): `NSFW_JOB_TIMEOUT_SECONDS` (default `280`, must stay below the Vercel `maxDuration`), `NSFW_POLL_INTERVAL_SECONDS`, `NSFW_OP_LOCK_TTL_SECONDS`, `NSFW_MAX_TOKENS` (default `1024`), `NSFW_TEMPERATURE` (default `0.7`, the model card's non-thinking preset), and `NSFW_OUTPUT_LIMIT_CHARS` (default `4096`).
- `BOT_USERNAME`: Used to recognize commands such as `/edit@BahlulBot` (default: `BahlulBot`).
- Audit trail: every received update (sender id/username/name, chat, first `AUDIT_SNIPPET_CHARS` characters of text) is appended to the Redis list `audit:log`, trimmed to `AUDIT_LOG_MAX_ENTRIES` (default `500`). Use `/audit [n]` (owner-only, `OWNER_IDS`) or `upstash redis lrange audit:log 0 20` to see who queried the bot — Vercel's log retention is too short for after-the-fact checks.

## Setup Instructions

1. **Clone the Repository**
   ```bash
   git clone https://github.com/lightzard/bahlul-bot.git
   cd bahlul-bot
   ```

2. **Install Dependencies**
   Ensure you have Python 3.10+ installed. Install the required packages:
   ```bash
   pip install -r requirements.txt
    ```
    The `requirements.txt` contains:
    ```
    fastapi
    python-telegram-bot>=20.0
    httpx
    uvicorn
    redis
    aiohttp
    openai
    ```

3. **Deploy the Image Backend (RunPod)**
   - Follow [runpod/README.md](runpod/README.md): build and push the worker image, create a Network Volume and a Serverless endpoint (Active Workers 0, Max Workers 1, idle timeout 300–600 s), then note your endpoint ID and RunPod API key.
   - The weights (Qwen Image 2.1 GGUF + text encoder + VAE) are pulled once from the public Hugging Face repo to the volume — nothing is re-uploaded or re-downloaded per request.

3b. **Deploy the Text Backend (RunPod, for /nsfw)**
   - Follow [runpod-text/README.md](runpod-text/README.md): build and push the worker image, create a second Serverless endpoint (RTX 4090, Active Workers 0, Max Workers 1, idle timeout 300–600 s) attached to the **same** Network Volume (same data center as the image endpoint), then note the text endpoint ID.
   - The HauhauCS Qwen3.5-4B GGUF (~2.7 GB) is pulled once to the shared volume; the existing 30 GB volume holds both stacks (~24 GB used).

4. **Set Up a Redis Instance**
   - Sign up for a free Redis database at https://upstash.com/.
   - Create a new Redis database and copy the `REDIS_URL` (e.g., `rediss://:<token>@<host>:<port>`).
   - This is used for storing conversation history to enable contextual responses, plus the image single-flight lock.

5. **Configure Secrets (Environment Variables)**
   - In Vercel, go to Dashboard > Project > Settings > Environment Variables.
   - Add the secrets (and optionally `WHITELIST_IDS`):
     - `TELEGRAM_TOKEN`: Your bot token from `@BotFather`.
     - `DEEPSEEK_API_KEY`: Your DeepSeek API key.
     - `RUNPOD_API_KEY`: Your RunPod API key (required for `/draw`, `/edit`, and `/nsfw`).
     - `RUNPOD_ENDPOINT_ID`: Your RunPod Serverless endpoint ID.
     - `RUNPOD_TEXT_ENDPOINT_ID`: Your RunPod Serverless endpoint ID for the HauhauCS text worker.
     - `REDIS_URL`: The Redis connection URL from Upstash.
     - `TAVILY_API_KEY`: (Optional) Your Tavily API key for live web search (https://www.tavily.com). Automatic recency search is disabled if omitted.
     - `WHITELIST_IDS`: (Optional) Comma-separated chat or user IDs allowed to use the bot. If unset, nobody can use it.
   - Edit `api/settings.py` for model names, limits, and other non-secret options.

6. **Deploy to Vercel**
   - Connect your GitHub repository to Vercel.
   - Deploy the `api/app.py` endpoint. `vercel.json` sets `maxDuration: 300` (seconds) so image jobs (including a RunPod cold start) fit in one function invocation. If your Vercel plan caps function duration lower (e.g., 60 s), lower `IMAGE_BACKEND_TIMEOUT_SECONDS` in `api/settings.py` accordingly and expect cold-start requests to fail.
   - Set the webhook for Telegram:
     ```bash
     curl -X POST “https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook?url=https://<your-vercel-app>.vercel.app/webhook”
     ```

7. **Configure Telegram Bot**
   - In Telegram, chat with `@BotFather`:
     - Create a bot and get the `TELEGRAM_TOKEN`.
     - Disable privacy mode for group chats: `/mybots` > Select your bot > Bot Settings > Group Privacy > Turn off.
   - Add the bot to a group and make it an admin (Settings > Administrators > Add Admin > `@BahlulBot` > Grant “Send Messages”).

8. **Test the Bot**
   - **Private Chat**:
     - Send: `/ask What is the capital of France?`
     - Expected: “The capital of France is Paris.”
     - Send: `What is its population?`
     - Expected: “~2.2 million” (context preserved via Redis).
     - Send: `/ask What is the latest Python version?`
     - Expected: A current answer with numbered sources like `[1]` and a `Sources:` list (triggers live web search).
     - Send: `/web What is happening in the news today?`
     - Expected: A forced live-search answer regardless of automatic detection.
     - Send: `/draw a cat astronaut, cinematic photo`
     - Expected: a photo reply (first request after idle may take a few minutes while the GPU worker cold-starts).
     - Send: `/drawlora a cat astronaut, cinematic photo`
     - Expected: a photo from the base int8 + LoRA stack (fails with a clear message if no LoRA is loaded on the worker).
     - Attach a photo captioned: `/edit make it night`
     - Expected: an edited version of the photo.
     - Attach a photo captioned: `/editlora make it night`
     - Expected: the LoRA-stack edit.
     - Send: `/nsfw write a story about a space smuggler` (or shorthand `/n`)
     - Expected: an uncensored reply (first request after idle may take ~30s while the GPU worker cold-starts).
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
  - `/nsfw` history lives under a separate namespace: `upstash redis keys nsfw:*`.
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
  - If you see a model-not-found error, confirm `deepseek-flash` is enabled for your account, or change `DEEPSEEK_MODEL` in `api/settings.py` to a valid model ID.
  - Ensure `openai` is installed (`pip show openai`).

- **Image Commands Failing** (`/draw`, `/drawlora`, `/edit`, `/editlora`):
  - Verify `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID` are set in Vercel.
  - Check logs for `Image backend error` / `RunPod job ... ended as FAILED`.
  - Check the worker logs in the RunPod console (endpoint → Logs) — the error string from ComfyUI is included in the bot's error reply.
  - `Image backend is not configured` → the two env vars above are missing.
  - First request after idle can take several minutes (GPU cold start). Subsequent requests reuse the warm worker for the configured idle window (5–10 min).
  - If the worker is busy you will get an "I'm still busy with the previous image request" reply — retry shortly.
  - See [runpod/README.md](runpod/README.md) for GPU-worker-side troubleshooting (OOM, GGUF loader errors, first-boot downloads).

- **NSFW Command Failing** (`/nsfw`, `/n`):
  - Verify `RUNPOD_API_KEY` and `RUNPOD_TEXT_ENDPOINT_ID` are set in Vercel.
  - Check logs for `Text backend error for /nsfw` / `RunPod job ... ended as FAILED`.
  - Check the worker logs in the RunPod console (text endpoint → Logs) — the error string from llama-server is included in the bot's error reply.
  - First request after idle can take ~30s (GPU cold start); a busy worker replies "I'm still busy with the previous /nsfw request" — retry shortly.
  - See [runpod-text/README.md](runpod-text/README.md) for text-worker-side troubleshooting (stale llama.cpp builds, quant swaps, first-boot downloads).

## License

This project is licensed under the MIT License.