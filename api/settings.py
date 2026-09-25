"""Central configuration for BahlulBot.

Secrets (tokens, API keys, connection URLs) and the WHITELIST_IDS access
list come from environment variables. Every other configurable value lives
in this file so it can be adjusted without touching the deployment
environment.
"""

import os

# ---------------------------------------------------------------------------
# Secrets (environment variables only)
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
# Secret connection string (it may contain a token), e.g. rediss://...
REDIS_URL = os.getenv("REDIS_URL")
# RunPod Serverless (Qwen Image 2.1 GGUF execution backend)
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY")
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID")
# Second RunPod endpoint (llama.cpp + HauhauCS Qwen3.5-4B GGUF) for /nsfw;
# reuses RUNPOD_API_KEY. See runpod-text/ in the repo root.
RUNPOD_TEXT_ENDPOINT_ID = os.getenv("RUNPOD_TEXT_ENDPOINT_ID")

# ---------------------------------------------------------------------------
# Access control (environment variable)
# ---------------------------------------------------------------------------
# Comma-separated chat or user IDs allowed to use the bot. Empty (or unset)
# locks the bot down; set the WHITELIST_IDS env var to authorize them.
WHITELIST_IDS = {
    id.strip() for id in os.getenv("WHITELIST_IDS", "").split(",") if id.strip()
}

# ---------------------------------------------------------------------------
# Chat (DeepSeek)
# ---------------------------------------------------------------------------
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CHAT_OUTPUT_LIMIT_CHARS = 4096
CONVERSATION_HISTORY_LIMIT = 10
CONVERSATION_TTL_SECONDS = 3600

# ---------------------------------------------------------------------------
# Image generation and editing (Qwen Image 2.1 GGUF via RunPod Serverless)
# ---------------------------------------------------------------------------
# Which provider api/image_backend uses: "runpod" (external GPU endpoint) or
# "dummy" (offline stub for tests/development).
IMAGE_BACKEND = os.getenv("IMAGE_BACKEND", "runpod")
RUNPOD_API_BASE_URL = "https://api.runpod.ai"
# Must stay below the Vercel function maxDuration (see vercel.json) so the
# Telegram reply can still be sent when a job barely fits the budget.
IMAGE_BACKEND_TIMEOUT_SECONDS = 280
IMAGE_POLL_INTERVAL_SECONDS = 3
# Global single-flight lock for all image commands. Covers a RunPod cold start
# (model download/load) so Telegram re-deliveries skip instead of queueing a
# second job on the single-GPU worker.
IMAGE_OP_LOCK_TTL_SECONDS = 600
# Defaults passed to the backend; the worker clamps them to the workflow.
# 25 steps matches the official ComfyUI Qwen-Image-2.1 templates.
QWEN_IMAGE_WIDTH = 1024
QWEN_IMAGE_HEIGHT = 1024
QWEN_IMAGE_STEPS = 25
QWEN_EDIT_STEPS = 25
# Send generated/edited images with a Telegram spoiler cover (tap-to-reveal)
# so explicit results stay blurred until deliberately opened.
IMAGE_HAS_SPOILER = True

# ---------------------------------------------------------------------------
# Uncensored text generation (/nsfw via HauhauCS Qwen3.5-4B on RunPod)
# ---------------------------------------------------------------------------
# Which provider api/text_backend uses: "runpod" (external GPU endpoint) or
# "dummy" (offline stub for tests/development).
TEXT_BACKEND = os.getenv("TEXT_BACKEND", "runpod")
# Must stay below the Vercel function maxDuration (see vercel.json) so the
# Telegram reply can still be sent when a job barely fits the budget.
NSFW_JOB_TIMEOUT_SECONDS = 280
NSFW_POLL_INTERVAL_SECONDS = 2
# Single-flight lock for /nsfw. Like the image lock, it covers a RunPod cold
# start so Telegram re-deliveries skip instead of queueing a second job on
# the single-GPU worker.
NSFW_OP_LOCK_TTL_SECONDS = 300
# Generation defaults; the worker clamps them server-side too.
NSFW_MAX_TOKENS = 1024
NSFW_TEMPERATURE = 0.7
NSFW_OUTPUT_LIMIT_CHARS = 4096

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
BOT_USERNAME = "BahlulBot"

# ---------------------------------------------------------------------------
# Live web search (Tavily)
# ---------------------------------------------------------------------------
WEB_SEARCH_AUTO_ENABLED = True
WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_CACHE_TTL_SECONDS = 600
WEB_SEARCH_DAILY_LIMIT = 50
WEB_SEARCH_TIMEOUT_SECONDS = 8
WEB_SEARCH_CONTEXT_MAX_CHARS = 8000
