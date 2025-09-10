import logging
import re
from typing import Optional
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from .config import Config
from .handlers import StartHandler, AskHandler, TextMessageHandler, GenerateHandler, DrawHandler, EditHandler

logger = logging.getLogger(__name__)

class BotManager:
    """Telegram bot initialization and management"""
    
    def __init__(self):
        self.config = Config()
        self.telegram_app: Optional[Application] = None
        self.handlers_initialized = False
    
    async def initialize_bot(self) -> Application:
        """Initialize Telegram bot application"""
        if not self.config.TELEGRAM_TOKEN:
            logger.error("TELEGRAM_TOKEN is not set")
            raise ValueError("TELEGRAM_TOKEN is not set")
        
        try:
            # Create application
            self.telegram_app = (
                Application.builder()
                .token(self.config.TELEGRAM_TOKEN)
                .build()
            )
            
            # Initialize the application
            logger.info("Initializing Telegram application")
            await self.telegram_app.initialize()
            
            # Initialize handlers
            await self._initialize_handlers()
            
            logger.info("Bot initialization completed")
            return self.telegram_app
            
        except Exception as e:
            logger.error(f"Error initializing bot: {str(e)}")
            raise
    
    async def _initialize_handlers(self):
        """Initialize all command and message handlers"""
        if self.handlers_initialized:
            return
        
        try:
            # Initialize handler instances
            start_handler = StartHandler()
            ask_handler = AskHandler()
            message_handler = TextMessageHandler()
            generate_handler = GenerateHandler()
            
            # Initialize draw handlers
            draw_handler = DrawHandler()
            draw_handler.set_command("/draw")
            
            gooddraw_handler = DrawHandler()
            gooddraw_handler.set_command("/gooddraw")
            
            # Initialize edit handlers
            edit_handler = EditHandler()
            edit_handler.set_command("/edit")
            edit_handler.set_telegram_app(self.telegram_app)
            
            goodedit_handler = EditHandler()
            goodedit_handler.set_command("/goodedit")
            goodedit_handler.set_telegram_app(self.telegram_app)
            
            # Initialize services for all handlers
            handlers = [
                start_handler, ask_handler, message_handler, generate_handler,
                draw_handler, gooddraw_handler, edit_handler, goodedit_handler
            ]
            
            for handler in handlers:
                await handler.initialize_services()
            
            # Add command handlers
            self.telegram_app.add_handler(CommandHandler("start", start_handler.handle))
            self.telegram_app.add_handler(CommandHandler("ask", ask_handler.handle))
            self.telegram_app.add_handler(CommandHandler("generate", generate_handler.handle))
            self.telegram_app.add_handler(CommandHandler("draw", draw_handler.handle))
            self.telegram_app.add_handler(CommandHandler("gooddraw", gooddraw_handler.handle))
            
            # Add message handlers for photo with captions
            self.telegram_app.add_handler(
                MessageHandler(
                    filters.PHOTO & 
                    filters.CaptionRegex(re.compile(r'^/goodedit(@BahlulBot)?\b.*', re.IGNORECASE)) & 
                    ~filters.VIA_BOT,
                    goodedit_handler.handle
                )
            )
            
            self.telegram_app.add_handler(
                MessageHandler(
                    filters.PHOTO & 
                    filters.CaptionRegex(re.compile(r'^/edit(@BahlulBot)?\b.*', re.IGNORECASE)) & 
                    ~filters.VIA_BOT,
                    edit_handler.handle
                )
            )
            
            # Add general text message handler
            self.telegram_app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler.handle)
            )
            
            self.handlers_initialized = True
            logger.info("Bot handlers added successfully")
            
        except Exception as e:
            logger.error(f"Error initializing handlers: {str(e)}")
            raise
    
    async def process_update(self, update: Update):
        """Process a single update"""
        if not self.telegram_app:
            raise RuntimeError("Bot not initialized")
        
        try:
            await self.telegram_app.process_update(update)
            logger.info("Update processed successfully")
        except Exception as e:
            logger.error(f"Error processing update: {str(e)}")
            raise
    
    async def shutdown(self):
        """Shutdown the bot application"""
        if self.telegram_app:
            try:
                await self.telegram_app.shutdown()
                logger.info("Bot application shutdown completed")
            except Exception as e:
                logger.error(f"Error shutting down bot: {str(e)}")
    
    def get_bot_app(self) -> Optional[Application]:
        """Get the current bot application instance"""
        return self.telegram_app