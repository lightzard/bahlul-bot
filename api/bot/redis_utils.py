import redis.asyncio as redis
from urllib.parse import urlparse
import json
import os
from bot.logger import logger

REDIS_URL = os.getenv("REDIS_URL")

# Initialize Redis client
async def init_redis():
    if not REDIS_URL:
        logger.warning("REDIS_URL not set, conversation history will not be stored")
        return None
    
    try:
        parsed_url = urlparse(REDIS_URL)
        if parsed_url.scheme not in ("redis", "rediss"):
            logger.error(f"Invalid REDIS_URL scheme: {parsed_url.scheme}. Expected redis:// or rediss://")
            return None
        
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        logger.info("Successfully connected to Redis")
        return redis_client
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {str(e)}")
        return None

# Function to get conversation history from Redis
async def get_conversation_history(redis_client, conversation_key: str) -> list:
    if redis_client is None:
        logger.warning("Redis client not initialized, returning empty history")
        return []
    try:
        history = await redis_client.get(conversation_key)
        if history:
            logger.info(f"Retrieved history for {conversation_key}: {history}")
            return json.loads(history)
        logger.info(f"No history found for {conversation_key}")
        return []
    except Exception as e:
        logger.error(f"Error retrieving conversation history for {conversation_key}: {str(e)}")
        return []

# Function to save conversation history to Redis
async def save_conversation_history(redis_client, conversation_key: str, conversation: list):
    if redis_client is None:
        logger.warning("Redis client not initialized, skipping history save")
        return
    try:
        conversation = conversation[-10:]  # Limit to last 10 messages
        await redis_client.set(conversation_key, json.dumps(conversation))
        await redis_client.expire(conversation_key, 3600)  # 1 hour expiry
        logger.info(f"Saved conversation history for {conversation_key}: {json.dumps(conversation)}")
    except Exception as e:
        logger.error(f"Error saving conversation history for {conversation_key}: {str(e)}")