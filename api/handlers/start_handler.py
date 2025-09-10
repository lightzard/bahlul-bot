from .base import BaseHandler
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)

class StartHandler(BaseHandler):
    """Handler for /start command"""
    
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process /start command"""
        logger.info(f"Processing /start command from chat_id={chat_id}, user_id={user_id}")
        
        response_text = "Hello! I'm BahlulBot, powered by Grok. Use /ask <your question> to get a response, or send a message in private chat."
        
        await self._send_text_response(update, response_text, message_thread_id)
        logger.info("Sent /start response")