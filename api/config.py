import os
from typing import List

class Config:
    """Configuration class for BahlulBot"""
    
    # Telegram Configuration
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    
    # AI API Configuration
    GROK_API_KEY: str = os.getenv("GROK_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GROK_MODEL: str = os.getenv("GROK_MODEL", "grok-3-mini-fast")
    
    # Redis Configuration
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    
    # Authentication Configuration
    WHITELIST_IDS: List[str] = os.getenv("WHITELIST_IDS", "").split(",") if os.getenv("WHITELIST_IDS") else []
    
    # Logging Configuration
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration"""
        required_vars = ["TELEGRAM_TOKEN", "GROK_API_KEY", "OPENAI_API_KEY"]
        missing_vars = []
        
        for var in required_vars:
            if not getattr(cls, var):
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        return True
    
    @classmethod
    def get_ai_model_config(cls, provider: str) -> dict:
        """Get AI model configuration for specific provider"""
        if provider == "grok":
            return {
                "api_key": cls.GROK_API_KEY,
                "model": cls.GROK_MODEL,
                "timeout": 3600
            }
        elif provider == "openai":
            return {
                "api_key": cls.OPENAI_API_KEY,
                "timeout": 3600
            }
        else:
            raise ValueError(f"Unsupported AI provider: {provider}")