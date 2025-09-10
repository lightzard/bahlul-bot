from .base import BaseHandler
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from ..image_processor import ImageProcessor
import logging

logger = logging.getLogger(__name__)

class GenerateHandler(BaseHandler):
    """Handler for /generate command"""
    
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process /generate command"""
        prompt = ' '.join(context.args) if context.args else None
        
        logger.info(f"Processing /generate command from chat_id={chat_id}, thread_id={message_thread_id}, prompt={prompt}")
        
        if not prompt:
            response_text = "Please provide a description after /generate (e.g., /generate A cat in a tree)"
            await self._send_text_response(update, response_text, message_thread_id)
            logger.info("Sent empty prompt warning")
            return
        
        try:
            # Get conversation history
            conversation = await self._get_conversation_history(chat_id, message_thread_id)
            conversation.append({"role": "user", "content": f"/generate {prompt}"})
            
            # Generate image
            image_data = await self.ai_service.generate_image(prompt, provider="grok")
            
            # Prepare response
            prepared_response = ImageProcessor.prepare_image_response(image_data)
            
            # Save to conversation history
            conversation.append({"role": "assistant", "content": f"Generated image: {image_data.get('revised_prompt', prompt)}"})
            await self._save_conversation_history(chat_id, message_thread_id, conversation)
            
            # Send image to user
            if prepared_response["type"] == "url":
                await self._send_photo_response(update, prepared_response["data"], prepared_response["caption"], message_thread_id)
            else:
                await self._send_photo_response(update, prepared_response["data"], prepared_response["caption"], message_thread_id)
            
            logger.info(f"Sent generated image to Telegram: {prepared_response['caption']}")
            
        except Exception as e:
            logger.error(f"Error processing /generate command: {str(e)}")
            raise