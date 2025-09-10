import logging
import json
import redis.asyncio as redis
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse
from .config import Config

logger = logging.getLogger(__name__)

class RedisManager:
    """Redis connection and conversation history management"""
    
    def __init__(self):
        self.redis_url = Config.REDIS_URL
        self.client: Optional[redis.Redis] = None
    
    async def initialize(self) -> Optional[redis.Redis]:
        """
        Initialize Redis client
        
        Returns:
            Redis client or None if connection fails
        """
        if not self.redis_url:
            logger.warning("REDIS_URL not set, conversation history will not be stored")
            return None
        
        try:
            # Parse REDIS_URL to validate
            parsed_url = urlparse(self.redis_url)
            if parsed_url.scheme not in ("redis", "rediss"):
                logger.error(f"Invalid REDIS_URL scheme: {parsed_url.scheme}. Expected redis:// or rediss://")
                return None
            
            # Create Redis client (rediss:// handles TLS automatically)
            self.client = redis.from_url(self.redis_url, decode_responses=True)
            # Test connection
            # await self.client.ping()
            logger.info("Successfully connected to Redis")
            return self.client
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            return None
    
    async def close(self):
        """Close Redis connection"""
        if self.client:
            await self.client.close()
            logger.info("Redis client closed")
    
    def get_conversation_key(self, chat_id: int, message_thread_id: Optional[int] = None) -> str:
        """
        Generate conversation key for Redis
        
        Args:
            chat_id: Telegram chat ID
            message_thread_id: Optional thread ID for group topics
            
        Returns:
            Redis key string
        """
        return f"chat:{chat_id}:{message_thread_id or 'main'}"
    
    async def get_conversation_history(self, conversation_key: str) -> List[Dict[str, Any]]:
        """
        Get conversation history from Redis
        
        Args:
            conversation_key: Redis key for conversation
            
        Returns:
            List of conversation messages
        """
        if self.client is None:
            logger.warning("Redis client not initialized, returning empty history")
            return []
        
        try:
            history = await self.client.get(conversation_key)
            if history:
                logger.info(f"Retrieved history for {conversation_key}: {history}")
                return json.loads(history)
            logger.info(f"No history found for {conversation_key}")
            return []
        except Exception as e:
            logger.error(f"Error retrieving conversation history for {conversation_key}: {str(e)}")
            return []
    
    async def save_conversation_history(self, conversation_key: str, conversation: List[Dict[str, Any]]):
        """
        Save conversation history to Redis
        
        Args:
            conversation_key: Redis key for conversation
            conversation: List of conversation messages
        """
        if self.client is None:
            logger.warning("Redis client not initialized, skipping history save")
            return
        
        try:
            # Limit history to last 10 messages to avoid token limits
            conversation = conversation[-10:]
            await self.client.set(conversation_key, json.dumps(conversation))
            # Set expiry to 1 hour to manage storage
            await self.client.expire(conversation_key, 3600)
            logger.info(f"Saved conversation history for {conversation_key}: {json.dumps(conversation)}")
        except Exception as e:
            logger.error(f"Error saving conversation history for {conversation_key}: {str(e)}")
    
    async def acquire_edit_lock(self, lock_key: str = "is_editing", timeout: int = 60) -> bool:
        """
        Acquire a lock for edit operations
        
        Args:
            lock_key: Key for the lock
            timeout: Lock timeout in seconds
            
        Returns:
            True if lock acquired, False otherwise
        """
        if self.client is None:
            logger.warning("Redis client not initialized, proceeding without lock")
            return True
        
        try:
            # Attempt to set lock only if it doesn't exist
            set_result = await self.client.set(lock_key, '1', nx=True, ex=timeout)
            if set_result:
                logger.info(f"Acquired edit lock: {lock_key}")
                return True
            else:
                logger.info(f"Another edit is in progress, lock {lock_key} not available")
                return False
        except Exception as e:
            logger.error(f"Error acquiring edit lock: {str(e)}")
            return True  # Proceed without lock on error
    
    async def release_edit_lock(self, lock_key: str = "is_editing"):
        """
        Release edit lock
        
        Args:
            lock_key: Key for the lock
        """
        if self.client is None:
            return
        
        try:
            await self.client.delete(lock_key)
            logger.info(f"Released edit lock: {lock_key}")
        except Exception as e:
            logger.error(f"Error releasing edit lock: {str(e)}")