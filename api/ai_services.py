import logging
from typing import Dict, Any, Optional
from openai import AsyncOpenAI
from xai_sdk import Client
from xai_sdk.chat import user, system, assistant
from xai_sdk.search import SearchParameters
from .config import Config

logger = logging.getLogger(__name__)

class AIService:
    """Unified interface for AI operations (xAI Grok and OpenAI)"""
    
    def __init__(self):
        self.config = Config()
        self.grok_client: Optional[Client] = None
        self.openai_client: Optional[AsyncOpenAI] = None
    
    def initialize_clients(self):
        """Initialize AI service clients"""
        try:
            if self.config.GROK_API_KEY:
                self.grok_client = Client(api_key=self.config.GROK_API_KEY, timeout=3600)
                logger.info("Grok client initialized")
            
            if self.config.OPENAI_API_KEY:
                self.openai_client = AsyncOpenAI(api_key=self.config.OPENAI_API_KEY)
                logger.info("OpenAI client initialized")
        except Exception as e:
            logger.error(f"Error initializing AI clients: {str(e)}")
            raise
    
    async def get_chat_response(self, conversation: list, provider: str = "grok") -> str:
        """
        Get chat response from AI provider
        
        Args:
            conversation: List of conversation messages with role and content
            provider: AI provider ("grok" or "openai")
            
        Returns:
            AI response text
        """
        if provider == "grok":
            return await self._get_grok_response(conversation)
        elif provider == "openai":
            return await self._get_openai_response(conversation)
        else:
            raise ValueError(f"Unsupported AI provider: {provider}")
    
    async def _get_grok_response(self, conversation: list) -> str:
        """Get response from Grok API"""
        if not self.grok_client:
            raise ValueError("Grok client not initialized")
        
        try:
            # Create chat session with search parameters
            chat = self.grok_client.chat.create(
                model=self.config.GROK_MODEL,
                search_parameters=SearchParameters(mode="auto")
            )
            
            # Add conversation messages
            for msg in conversation:
                if msg["role"] == "user":
                    chat.append(user(msg["content"]))
                elif msg["role"] == "system":
                    chat.append(system(msg["content"][0]["text"]))
                elif msg["role"] == "assistant":
                    chat.append(assistant(msg["content"]))
            
            # Get response
            response = chat.sample()
            logger.info(f"Got response from Grok: {response.content}")
            return response.content
            
        except Exception as e:
            logger.error(f"Error getting Grok response: {str(e)}")
            raise
    
    async def _get_openai_response(self, conversation: list) -> str:
        """Get response from OpenAI API"""
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")
        
        try:
            # Convert conversation format to OpenAI format
            messages = []
            for msg in conversation:
                if msg["role"] == "system":
                    messages.append({"role": "system", "content": msg["content"][0]["text"]})
                else:
                    messages.append({"role": msg["role"], "content": msg["content"]})
            
            # Get response
            response = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",  # Default model for chat
                messages=messages,
                max_tokens=4096
            )
            
            content = response.choices[0].message.content
            logger.info(f"Got response from OpenAI: {content}")
            return content
            
        except Exception as e:
            logger.error(f"Error getting OpenAI response: {str(e)}")
            raise
    
    async def generate_image(self, prompt: str, provider: str = "openai", quality: str = "low") -> Dict[str, Any]:
        """
        Generate image using AI provider
        
        Args:
            prompt: Image description
            provider: AI provider ("openai" or "grok")
            quality: Image quality ("low" or "auto")
            
        Returns:
            Dictionary with image data
        """
        if provider == "openai":
            return await self._generate_openai_image(prompt, quality)
        elif provider == "grok":
            return await self._generate_grok_image(prompt)
        else:
            raise ValueError(f"Unsupported image generation provider: {provider}")
    
    async def _generate_openai_image(self, prompt: str, quality: str = "low") -> Dict[str, Any]:
        """Generate image using OpenAI API"""
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")
        
        try:
            response = await self.openai_client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                n=1,
                size="1024x1024",
                quality=quality,
                moderation="low"
            )
            
            image_base64 = response.data[0].b64_json
            logger.info(f"Generated image with OpenAI, prompt: {prompt}")
            
            return {
                "provider": "openai",
                "image_base64": image_base64,
                "prompt": prompt,
                "quality": quality
            }
            
        except Exception as e:
            logger.error(f"Error generating image with OpenAI: {str(e)}")
            raise
    
    async def _generate_grok_image(self, prompt: str) -> Dict[str, Any]:
        """Generate image using Grok API"""
        if not self.grok_client:
            raise ValueError("Grok client not initialized")
        
        try:
            response = self.grok_client.image.sample(
                model="grok-2-image",
                prompt=prompt,
                image_format="url"
            )
            
            logger.info(f"Generated image with Grok, prompt: {prompt}, revised_prompt: {response.prompt}")
            
            return {
                "provider": "grok",
                "image_url": response.url,
                "prompt": prompt,
                "revised_prompt": response.prompt
            }
            
        except Exception as e:
            logger.error(f"Error generating image with Grok: {str(e)}")
            raise
    
    async def edit_image(self, image_data: bytes, prompt: str, quality: str = "low", input_fidelity: str = "standard") -> Dict[str, Any]:
        """
        Edit image using OpenAI API
        
        Args:
            image_data: Image bytes
            prompt: Edit instruction
            quality: Image quality ("low" or "auto")
            input_fidelity: Input fidelity ("standard" or "high")
            
        Returns:
            Dictionary with edited image data
        """
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")
        
        try:
            import io
            
            image_file = io.BytesIO(image_data)
            image_file.name = "image.png"
            
            response = await self.openai_client.images.edit(
                model="gpt-image-1",
                image=image_file,
                prompt=prompt,
                n=1,
                quality=quality,
                size='1024x1024',
                input_fidelity=input_fidelity
            )
            
            image_base64 = response.data[0].b64_json
            logger.info("Successfully edited image with OpenAI")
            
            return {
                "provider": "openai",
                "image_base64": image_base64,
                "prompt": prompt,
                "quality": quality,
                "input_fidelity": input_fidelity
            }
            
        except Exception as e:
            logger.error(f"Error editing image: {str(e)}")
            raise