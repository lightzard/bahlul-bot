import logging
from typing import List
from .config import Config

logger = logging.getLogger(__name__)

class AuthService:
    """Authentication and authorization service for BahlulBot"""
    
    def __init__(self):
        self.whitelist_ids = Config.WHITELIST_IDS
    
    def is_whitelisted(self, chat_id: int, user_id: int) -> bool:
        """
        Check if chat_id or user_id is in whitelist
        
        Args:
            chat_id: Telegram chat ID
            user_id: Telegram user ID
            
        Returns:
            bool: True if authorized, False otherwise
        """
        whitelisted = str(chat_id) in self.whitelist_ids or str(user_id) in self.whitelist_ids
        logger.info(f"Checking whitelist: chat_id={chat_id}, user_id={user_id}, whitelisted={whitelisted}")
        return whitelisted
    
    def authorize_and_log_unauthorized(self, chat_id: int, user_id: int) -> bool:
        """
        Check authorization and log unauthorized attempts
        
        Args:
            chat_id: Telegram chat ID
            user_id: Telegram user ID
            
        Returns:
            bool: True if authorized, False otherwise
        """
        if not self.is_whitelisted(chat_id, user_id):
            logger.info(f"Unauthorized access attempt: chat_id={chat_id}, user_id={user_id}")
            return False
        return True
    
    def get_unauthorized_message(self) -> str:
        """Get standard unauthorized message"""
        return "Sorry, you are not authorized to use this bot."
    
    def add_to_whitelist(self, id_str: str) -> bool:
        """
        Add ID to whitelist (for runtime modifications)
        
        Args:
            id_str: ID to add to whitelist
            
        Returns:
            bool: True if added successfully
        """
        if id_str not in self.whitelist_ids:
            self.whitelist_ids.append(id_str)
            logger.info(f"Added {id_str} to whitelist")
            return True
        return False
    
    def remove_from_whitelist(self, id_str: str) -> bool:
        """
        Remove ID from whitelist (for runtime modifications)
        
        Args:
            id_str: ID to remove from whitelist
            
        Returns:
            bool: True if removed successfully
        """
        if id_str in self.whitelist_ids:
            self.whitelist_ids.remove(id_str)
            logger.info(f"Removed {id_str} from whitelist")
            return True
        return False
    
    def get_whitelist(self) -> List[str]:
        """Get current whitelist"""
        return self.whitelist_ids.copy()