"""Provider-agnostic interface for image generation and editing."""

import logging
from typing import Protocol, runtime_checkable

from api import settings

logger = logging.getLogger(__name__)


class ImageBackendError(Exception):
    """Raised when the image backend fails, times out, or is misconfigured."""


@runtime_checkable
class ImageBackend(Protocol):
    async def generate_image(self, prompt: str, **options) -> bytes:
        """Text-to-image: return the generated image as PNG bytes."""
        ...

    async def edit_image(self, image_bytes: bytes, prompt: str, **options) -> bytes:
        """Image editing: return the edited image as PNG bytes."""
        ...


def get_image_backend() -> ImageBackend:
    """Return the image backend selected by settings.IMAGE_BACKEND."""
    name = (settings.IMAGE_BACKEND or "").strip().lower()
    if name == "runpod":
        from api.image_backend.runpod_backend import RunPodBackend

        return RunPodBackend()
    if name == "dummy":
        from api.image_backend.dummy_backend import DummyBackend

        return DummyBackend()
    raise ImageBackendError(f"Unknown IMAGE_BACKEND: {settings.IMAGE_BACKEND!r}")
