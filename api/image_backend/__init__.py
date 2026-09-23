"""Image generation/editing backend abstraction.

Telegram command handlers depend only on generate_image()/edit_image() from
this package; the concrete runtime is selected via settings.IMAGE_BACKEND.
Today the real backend is a RunPod Serverless endpoint running ComfyUI with
Qwen Image 2.1 GGUF (see runpod/ in the repo root); Hugging Face only hosts
the model files, it is not the inference runtime.
"""

from api.image_backend.base import ImageBackend, ImageBackendError, get_image_backend

__all__ = [
    "ImageBackend",
    "ImageBackendError",
    "generate_image",
    "edit_image",
    "get_image_backend",
]


async def generate_image(prompt: str, **options) -> bytes:
    """Generate an image from a text prompt and return PNG bytes."""
    backend = get_image_backend()
    return await backend.generate_image(prompt, **options)


async def edit_image(image_bytes: bytes, prompt: str, **options) -> bytes:
    """Edit the given image following the prompt and return PNG bytes."""
    backend = get_image_backend()
    return await backend.edit_image(image_bytes, prompt, **options)
