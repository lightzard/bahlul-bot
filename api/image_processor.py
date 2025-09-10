import logging
import base64
import io
import aiohttp
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class ImageProcessor:
    """Image processing and handling utilities"""
    
    @staticmethod
    async def download_image_from_telegram(file_url: str) -> bytes:
        """
        Download image from Telegram file URL
        
        Args:
            file_url: Telegram file URL
            
        Returns:
            Image bytes
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to download image: HTTP {resp.status}")
                    image_data = await resp.read()
                    
            logger.info(f"Downloaded image from Telegram, size: {len(image_data)} bytes")
            return image_data
            
        except Exception as e:
            logger.error(f"Error downloading image from Telegram: {str(e)}")
            raise
    
    @staticmethod
    def base64_to_bytes(base64_string: str) -> bytes:
        """
        Convert base64 string to bytes
        
        Args:
            base64_string: Base64 encoded string
            
        Returns:
            Decoded bytes
        """
        try:
            image_bytes = base64.b64decode(base64_string)
            logger.info(f"Converted base64 to bytes, size: {len(image_bytes)} bytes")
            return image_bytes
        except Exception as e:
            logger.error(f"Error converting base64 to bytes: {str(e)}")
            raise
    
    @staticmethod
    def bytes_to_base64(image_bytes: bytes) -> str:
        """
        Convert bytes to base64 string
        
        Args:
            image_bytes: Image bytes
            
        Returns:
            Base64 encoded string
        """
        try:
            base64_string = base64.b64encode(image_bytes).decode('utf-8')
            logger.info(f"Converted bytes to base64, length: {len(base64_string)}")
            return base64_string
        except Exception as e:
            logger.error(f"Error converting bytes to base64: {str(e)}")
            raise
    
    @staticmethod
    def create_image_file(image_bytes: bytes, filename: str = "image.png") -> io.BytesIO:
        """
        Create image file object from bytes
        
        Args:
            image_bytes: Image bytes
            filename: Filename for the image
            
        Returns:
            BytesIO file object
        """
        try:
            image_file = io.BytesIO(image_bytes)
            image_file.name = filename
            logger.info(f"Created image file: {filename}")
            return image_file
        except Exception as e:
            logger.error(f"Error creating image file: {str(e)}")
            raise
    
    @staticmethod
    def extract_prompt_from_caption(caption: str, command: str) -> str:
        """
        Extract prompt from image caption
        
        Args:
            caption: Image caption
            command: Command name (e.g., "/edit", "/goodedit")
            
        Returns:
            Extracted prompt
        """
        try:
            # Handle both "/edit" and "/edit@BahlulBot" formats
            if command in caption:
                if f"{command}@BahlulBot" in caption.lower():
                    prompt_start = len(f"{command}@BahlulBot")
                else:
                    prompt_start = len(command)
                prompt = caption[prompt_start:].strip()
                logger.info(f"Extracted prompt from caption: '{prompt}'")
                return prompt
            else:
                logger.warning(f"Command '{command}' not found in caption: '{caption}'")
                return ""
        except Exception as e:
            logger.error(f"Error extracting prompt from caption: {str(e)}")
            return ""
    
    @staticmethod
    def prepare_image_response(image_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare image response for Telegram
        
        Args:
            image_data: Image data from AI service
            
        Returns:
            Prepared response data
        """
        try:
            if image_data["provider"] == "openai":
                # Convert base64 to bytes for Telegram
                image_bytes = ImageProcessor.base64_to_bytes(image_data["image_base64"])
                return {
                    "type": "bytes",
                    "data": image_bytes,
                    "caption": f"Generated: {image_data['prompt']}"
                }
            elif image_data["provider"] == "grok":
                # Return URL for Telegram
                return {
                    "type": "url",
                    "data": image_data["image_url"],
                    "caption": f"Generated: {image_data['prompt']} (Revised: {image_data['revised_prompt']})"
                }
            else:
                raise ValueError(f"Unsupported image provider: {image_data['provider']}")
                
        except Exception as e:
            logger.error(f"Error preparing image response: {str(e)}")
            raise
    
    @staticmethod
    async def get_telegram_photo_info(update_message_photo) -> Dict[str, Any]:
        """
        Get photo information from Telegram update
        
        Args:
            update_message_photo: Telegram photo object
            
        Returns:
            Dictionary with photo information
        """
        try:
            # Get the highest resolution photo
            photo = update_message_photo[-1]
            file_info = await photo.get_file()
            
            return {
                "file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "file_size": photo.file_size,
                "file_path": file_info.file_path
            }
            
        except Exception as e:
            logger.error(f"Error getting Telegram photo info: {str(e)}")
            raise