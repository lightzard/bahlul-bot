from .base import BaseHandler
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)

class AskHandler(BaseHandler):
    """Handler for /ask command"""
    
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process /ask command"""
        query = ' '.join(context.args) if context.args else None
        
        logger.info(f"Processing /ask command from chat_id={chat_id}, thread_id={message_thread_id}, query={query}")
        
        if not query:
            response_text = "Please provide a question after /ask (e.g., /ask What is the capital of France?)"
            await self._send_text_response(update, response_text, message_thread_id)
            logger.info("Sent empty query warning")
            return
        
        try:
            # Get conversation history
            conversation = await self._get_conversation_history(chat_id, message_thread_id)
            conversation.append({"role": "user", "content": query})
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
            logger.error(f"Error processing /ask command: {str(e)}")
            raise