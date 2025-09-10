from .base import BaseHandler
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)

class MessageHandler(BaseHandler):
    """Handler for general text messages"""
    
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process general text message"""
        message_text = update.message.text
        
        logger.info(f"Processing message from chat_id={chat_id}, thread_id={message_thread_id}: {message_text}")
        
        try:
            # Get conversation history
            conversation = await self._get_conversation_history(chat_id, message_thread_id)
            conversation.append({"role": "user", "content": message_text})
            conversation.append({"role": "system", "content": [{"type": "text", "text": "Your maximum output is 4096 characters."}]})
            
            # Get AI response
            response = await self.ai_service.get_chat_response(conversation, provider="grok")
            
            # Remove system message and save assistant response
            conversation.pop()
            conversation.append({"role": "assistant", "content": response})
            await self._save_conversation_history(chat_id, message_thread_id, conversation)
            
            # Send response to user
            await self._send_text_response(update, response, message_thread_id)
            logger.info(f"Sent response to Telegram: {response}")
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            raise