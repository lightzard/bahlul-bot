from fastapi import FastAPI, Request, Response
from telegram import Update
import logging
from .config import Config
from .bot_manager import BotManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI()

# Initialize bot manager
bot_manager = BotManager()

# Validate configuration on startup
@app.on_event("startup")
async def startup():
    """Validate configuration on application startup"""
    try:
        Config.validate()
        logger.info("Configuration validated successfully")
    except ValueError as e:
        logger.error(f"Configuration validation failed: {str(e)}")
        raise

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Telegram webhook endpoint
    
    Handles incoming updates from Telegram Bot API
    """
    try:
        # Initialize bot for this request
        telegram_app = await bot_manager.initialize_bot()
        
        # Parse update from request
        update_json = await request.json()
        logger.info(f"Received update: {update_json}")
        
        # Create Update object
        update = Update.de_json(update_json, telegram_app.bot)
        
        # Process the update
        await bot_manager.process_update(update)
        
        # Shutdown bot to clean up resources
        await bot_manager.shutdown()
        
        return Response(status_code=200)
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        # Ensure bot is shutdown even on error
        try:
            await bot_manager.shutdown()
        except Exception as shutdown_error:
            logger.error(f"Error during shutdown: {str(shutdown_error)}")
        
        return Response(content=f"Error: {str(e)}", status_code=500)

@app.on_event("shutdown")
async def shutdown():
    """Application shutdown handler"""
    logger.info("Application shutdown")
    try:
        await bot_manager.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")