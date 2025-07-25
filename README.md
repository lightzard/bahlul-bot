# Telegram Grok Bot

A webhook-based Telegram bot that integrates with the Grok 4 model, hosted on Vercel.

## Setup
1. Install dependencies: `pip install -r requirements.txt`
2. Set environment variables: `TELEGRAM_TOKEN` and `GROK_API_KEY`
3. Deploy to Vercel via GitHub integration
4. Set webhook: `curl -X POST "https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook?url=https://<your-vercel-url>"`

## Requirements
- Python 3.9+
- Vercel account
- Telegram bot token
- Grok API key