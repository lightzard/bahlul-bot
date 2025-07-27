BahlulBot
BahlulBot is a Telegram bot powered by the Grok API, built with FastAPI and hosted on Vercel. It responds to user messages and commands in private and group chats, leveraging the Grok API for intelligent responses. The bot supports conversation context, maintaining a history of interactions to provide coherent responses.
Features

Command Handling: Responds to /start and /ask <question> commands in private and group chats.
Text Message Handling: Processes regular text messages in private chats and group chats (if privacy mode is disabled and the bot is an admin).
Conversation Context: Stores up to 10 messages per chat (private or group, including topic threads) in a Redis database with a 1-hour expiry, enabling contextual responses from the Grok API.
Webhook-Based: Uses FastAPI to handle Telegram webhook updates, optimized for Vercel’s serverless environment.
Grok API Integration: Powered by xAI’s Grok API (default model: grok-3) for generating responses.
Group Chat Support: Handles group messages and topic threads (supergroups) when properly configured.

Requirements
Dependencies

fastapi: For building the webhook-based API.
python-telegram-bot>=20.0: For interacting with the Telegram Bot API.
httpx: For making asynchronous HTTP requests to the Grok API.
uvicorn: For running the FastAPI application.
redis: For storing conversation history in a Redis database.

Environment Variables

TELEGRAM_TOKEN: Your Telegram bot token from @BotFather.
GROK_API_KEY: Your xAI Grok API key (see https://x.ai/api for details).
GROK_MODEL: The Grok model to use (default: grok-3).
REDIS_URL: The connection URL for your Redis instance (e.g., rediss://:<token>@<host>:<port> from Upstash).

Setup Instructions

Clone the Repository
git clone https://github.com/lightzard/bahlul-bot.git
cd bahlul-bot


Install DependenciesEnsure you have Python 3.8+ installed. Install the required packages:
pip install -r api/requirements.txt

The requirements.txt should contain:
fastapi
python-telegram-bot>=20.0
httpx
uvicorn
redis


Set Up a Redis Instance

Sign up for a free Redis database at https://upstash.com/.
Create a new Redis database and copy the REDIS_URL (e.g., rediss://:<token>@<host>:<port>).
This is used for storing conversation history to enable contextual responses.


Configure Environment Variables

In Vercel, go to Dashboard > Project > Settings > Environment Variables.
Add:
TELEGRAM_TOKEN: Your bot token from @BotFather.
GROK_API_KEY: Your Grok API key.
GROK_MODEL: Set to grok-3 (or another valid model; see https://x.ai/api).
REDIS_URL: The Redis connection URL from Upstash.




Deploy to Vercel

Connect your GitHub repository to Vercel.
Deploy the api/app.py endpoint.
Set the webhook for Telegram:curl -X POST "https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook?url=https://<your-vercel-app>.vercel.app/webhook"




Configure Telegram Bot

In Telegram, chat with @BotFather:
Create a bot and get the TELEGRAM_TOKEN.
Disable privacy mode for group chats: /mybots > Select your bot > Bot Settings > Group Privacy > Turn off.


Add the bot to a group and make it an admin (Settings > Administrators > Add Admin > @BahlulBot > Grant “Send Messages”).


Test the Bot

Private Chat:
Send: /ask What is the capital of France?
Expected: “The capital of France is Paris.”
Send: What is its population?
Expected: “~2.2 million” (context preserved via Redis).


Group Chat (with privacy mode off and bot as admin):
Send: /ask What is AI?
Expected: “AI is…”
Send: Give an example.
Expected: An AI example, using context.
Send: hello (should respond if privacy mode is off).




Verify Redis Data

Use Upstash Dashboard or CLI:upstash redis keys chat:*
upstash redis get chat:<chat_id>:main


Expected: JSON like [{"role": "user", "content": "What is the capital of France?"}, {"role": "assistant", "content": "The capital of France is Paris."}, ...].


Check Vercel logs:vercel logs <your-app>.vercel.app


Look for: Successfully connected to Redis, Saved conversation history for chat:....





Troubleshooting

Redis Errors:

If logs show Failed to connect to Redis, verify REDIS_URL in Vercel matches Upstash’s rediss:// URL.
Test Redis locally:import redis.asyncio as redis
import asyncio
async def test_redis():
    client = redis.from_url("<your-rediss-url>", decode_responses=True)
    await client.ping()
    print("Connected")
    await client.close()
asyncio.run(test_redis())




Group Messages Not Working:

Ensure privacy mode is off via @BotFather.
Re-add bot as admin in the group.
Test with @BahlulBot hello and check logs for Processing message from chat type group.


Conversation Context Not Preserved:

Check logs for Saved conversation history or Retrieved history.
Verify Redis data in Upstash Dashboard.
Ensure REDIS_URL is correct.


Grok API Issues:

Verify GROK_API_KEY and GROK_MODEL (see https://x.ai/api).
Check logs for HTTP errors from https://api.x.ai/v1/chat/completions.



License
This project is licensed under the MIT License.