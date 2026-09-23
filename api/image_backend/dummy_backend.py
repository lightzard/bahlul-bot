"""Offline image backend for tests and local development.

Returns a small deterministic PNG so command handlers and wiring can be
exercised without a GPU endpoint. Select with IMAGE_BACKEND=dummy.
"""

import struct
import zlib


def _make_png(width: int = 8, height: int = 8, rgb: tuple = (255, 0, 255)) -> bytes:
    """Build a minimal valid PNG without external dependencies."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return length + tag + data + crc

    scanlines = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines, 9))
        + chunk(b"IEND", b"")
    )


class DummyBackend:
    """Deterministic stub: every request returns the same PNG."""

    def __init__(self) -> None:
        self._png = _make_png()

    async def generate_image(self, prompt: str, **options) -> bytes:
        return self._png

    async def edit_image(self, image_bytes: bytes, prompt: str, **options) -> bytes:
        return self._png
