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
GROK_API_KEY = os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
# Secret connection string (it may contain a token), e.g. rediss://...
REDIS_URL = os.getenv("REDIS_URL")

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
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CHAT_OUTPUT_LIMIT_CHARS = 4096
CONVERSATION_HISTORY_LIMIT = 10
CONVERSATION_TTL_SECONDS = 3600

# ---------------------------------------------------------------------------
# Image generation (xAI Grok, /generate)
# ---------------------------------------------------------------------------
GROK_IMAGE_MODEL = "grok-2-image"
GROK_IMAGE_FORMAT = "url"
GROK_TIMEOUT_SECONDS = 3600

# ---------------------------------------------------------------------------
# Image generation and editing (OpenAI, /draw, /gooddraw, /edit, /goodedit)
# ---------------------------------------------------------------------------
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_IMAGE_SIZE = "1024x1024"
OPENAI_IMAGE_QUALITY = "low"
OPENAI_IMAGE_HIGH_QUALITY = "auto"
OPENAI_IMAGE_MODERATION = "low"
OPENAI_EDIT_QUALITY = "low"
OPENAI_EDIT_HIGH_QUALITY = "auto"
OPENAI_EDIT_HIGH_FIDELITY = "high"
IMAGE_EDIT_LOCK_TTL_SECONDS = 60

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
