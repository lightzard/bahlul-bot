from abc import ABC, abstractmethod
import logging
from typing import Optional, Dict, Any
from telegram import Update
from telegram.ext import ContextTypes
from ..config import Config
from ..auth import AuthService
from ..redis_manager import RedisManager
from ..ai_services import AIService

logger = logging.getLogger(__name__)

class BaseHandler(ABC):
    """Base class for all command handlers"""
    
    def __init__(self):
        self.config = Config()
        self.auth_service = AuthService()
        self.redis_manager = RedisManager()
        self.ai_service = AIService()
    
    async def initialize_services(self):
        """Initialize required services"""
        self.ai_service.initialize_clients()
        await self.redis_manager.initialize()
    
    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Main handler method
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        try:
            if not self._validate_update(update):
                return
            
            chat_id, user_id, message_thread_id = self._extract_chat_info(update)
            
            if not self.auth_service.authorize_and_log_unauthorized(chat_id, user_id):
                await self._send_unauthorized_response(update, message_thread_id)
                return
            
            await self._process_command(update, context, chat_id, user_id, message_thread_id)
            
        except Exception as e:
            logger.error(f"Error in {self.__class__.__name__}: {str(e)}")
            await self._send_error_response(update, e)
    
    def _validate_update(self, update: Update) -> bool:
        """Validate update has required message"""
        if update.message is None:
            logger.info(f"Received update with no message content in {self.__class__.__name__}")
            return False
        return True
    
    def _extract_chat_info(self, update: Update) -> tuple:
        """Extract chat information from update"""
        chat_id = update.message.chat.id
        user_id = update.message.from_user.id
        message_thread_id = update.message.message_thread_id
        return chat_id, user_id, message_thread_id
    
    async def _send_unauthorized_response(self, update: Update, message_thread_id: Optional[int]):
        """Send unauthorized response to user"""
        reply_params = {"text": self.auth_service.get_unauthorized_message()}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
    
    async def _send_error_response(self, update: Update, error: Exception):
        """Send error response to user"""
        message_thread_id = update.message.message_thread_id if update.message else None
        reply_params = {"text": f"Error processing your request: {str(error)}"}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
    
    async def _send_text_response(self, update: Update, text: str, message_thread_id: Optional[int] = None):
        """Send text response to user"""
        reply_params = {"text": text}
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_text(**reply_params)
    
    async def _send_photo_response(self, update: Update, photo_data, caption: str = "", message_thread_id: Optional[int] = None):
        """Send photo response to user"""
        reply_params = {"photo": photo_data}
        if caption:
            reply_params["caption"] = caption
        if message_thread_id:
            reply_params["message_thread_id"] = message_thread_id
        await update.message.reply_photo(**reply_params)
    
    async def _get_conversation_history(self, chat_id: int, message_thread_id: Optional[int]) -> list:
        """Get conversation history from Redis"""
        conversation_key = self.redis_manager.get_conversation_key(chat_id, message_thread_id)
        return await self.redis_manager.get_conversation_history(conversation_key)
    
    async def _save_conversation_history(self, chat_id: int, message_thread_id: Optional[int], conversation: list):
        """Save conversation history to Redis"""
        conversation_key = self.redis_manager.get_conversation_key(chat_id, message_thread_id)
        await self.redis_manager.save_conversation_history(conversation_key, conversation)
    
    @abstractmethod
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process the specific command - must be implemented by subclasses"""
        pass