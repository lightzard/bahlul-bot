from .base import BaseHandler
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from ..image_processor import ImageProcessor
import logging

logger = logging.getLogger(__name__)

class DrawHandler(BaseHandler):
    """Handler for /draw and /gooddraw commands"""
    
    def __init__(self):
        super().__init__()
        self.command = None
    
    def set_command(self, command: str):
        """Set the specific draw command"""
        self.command = command
    
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process /draw or /gooddraw command"""
        prompt = ' '.join(context.args) if context.args else None
        
        logger.info(f"Processing {self.command} command from chat_id={chat_id}, thread_id={message_thread_id}, prompt={prompt}")
        
        if not prompt:
            response_text = f"Please provide a description after {self.command} (e.g., {self.command} A cute baby sea otter)"
            await self._send_text_response(update, response_text, message_thread_id)
            logger.info("Sent empty prompt warning")
            return
        
        try:
            # Determine quality based on command
            quality = "auto" if self.command == "/gooddraw" else "low"
            
            # Get conversation history
            conversation = await self._get_conversation_history(chat_id, message_thread_id)
            conversation.append({"role": "user", "content": f"{self.command} {prompt}"})
            
            # Generate image
            image_data = await self.ai_service.generate_image(prompt, provider="openai", quality=quality)
            
            # Prepare response
            prepared_response = ImageProcessor.prepare_image_response(image_data)
            
            # Save to conversation history
            conversation.append({"role": "assistant", "content": f"Generated image with prompt: {prompt}"})
            await self._save_conversation_history(chat_id, message_thread_id, conversation)
            
            # Send image to user
            await self._send_photo_response(update, prepared_response["data"], prepared_response["caption"], message_thread_id)
            logger.info(f"Sent generated image to Telegram (base64 length: {len(image_data['image_base64'])})")
            
        except Exception as e:
            logger.error(f"Error processing {self.command} command: {str(e)}")
            raise