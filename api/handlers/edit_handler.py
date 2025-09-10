from .base import BaseHandler
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from ..image_processor import ImageProcessor
import logging

logger = logging.getLogger(__name__)

class EditHandler(BaseHandler):
    """Handler for /edit and /goodedit commands"""
    
    def __init__(self):
        super().__init__()
        self.command = None
        self.telegram_app = None
    
    def set_command(self, command: str):
        """Set the specific edit command"""
        self.command = command
    
    def set_telegram_app(self, telegram_app):
        """Set telegram app for webhook info"""
        self.telegram_app = telegram_app
    
    async def _process_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, user_id: int, message_thread_id: Optional[int]):
        """Process /edit or /goodedit command"""
        caption = update.message.caption
        
        logger.info(f"Processing {self.command} command from chat_id={chat_id}, thread_id={message_thread_id}, caption={caption}")
        
        try:
            # Check webhook pending updates
            if self.telegram_app:
                webhook_info = await self.telegram_app.bot.get_webhook_info()
                if webhook_info.pending_update_count > 1:
                    logger.info(f"Pending updates found: {webhook_info.pending_update_count}. Returning 200 immediately.")
                    return
            
            # Extract prompt from caption
            prompt = ImageProcessor.extract_prompt_from_caption(caption, self.command)
            
            if not prompt:
                response_text = f"Please provide a description after {self.command} in the caption"
                await self._send_text_response(update, response_text, message_thread_id)
                logger.info("Sent empty prompt warning")
                return
            
            # Get photo information
            photo_info = await ImageProcessor.get_telegram_photo_info(update.message.photo)
            
            # Acquire edit lock
            lock_acquired = await self.redis_manager.acquire_edit_lock()
            if not lock_acquired:
                logger.info("Another edit is in progress, skipping this request")
                return
            
            try:
                # Download image from Telegram
                image_bytes = await ImageProcessor.download_image_from_telegram(photo_info["file_path"])
                
                # Determine quality and input fidelity based on command
                quality = "auto" if self.command == "/goodedit" else "low"
                input_fidelity = "high" if self.command == "/goodedit" else "standard"
                
                # Edit image
                image_data = await self.ai_service.edit_image(image_bytes, prompt, quality, input_fidelity)
                
                # Prepare response
                prepared_response = ImageProcessor.prepare_image_response(image_data)
                
                # Send edited image to user
                await self._send_photo_response(update, prepared_response["data"], prepared_response["caption"], message_thread_id)
                logger.info(f"Sent edited image to Telegram (base64 length: {len(image_data['image_base64'])})")
                
            finally:
                # Release edit lock
                await self.redis_manager.release_edit_lock()
                
        except Exception as e:
            logger.error(f"Error processing {self.command} command: {str(e)}")
            raise