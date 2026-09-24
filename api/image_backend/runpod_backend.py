"""RunPod Serverless image backend.

Submits jobs to a RunPod Serverless endpoint (running ComfyUI with the
Qwen Image 2.1 GGUF weights from Hugging Face) and polls until the job
finishes. See runpod/README.md in the repo root for the worker deployment.
"""

import asyncio
import base64
import logging
import time

import aiohttp

from api import settings
from api.image_backend.base import ImageBackendError

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"FAILED", "CANCELLED", "TIMED_OUT"}


class RunPodBackend:
    """Image backend driven by the RunPod Serverless REST API."""

    async def generate_image(self, prompt: str, **options) -> bytes:
        job_input = {
            "task": "text2img_lora" if options.get("lora") else "text2img",
            "prompt": prompt,
            "width": options.get("width", settings.QWEN_IMAGE_WIDTH),
            "height": options.get("height", settings.QWEN_IMAGE_HEIGHT),
            "steps": options.get("steps", settings.QWEN_IMAGE_STEPS),
        }
        if options.get("seed") is not None:
            job_input["seed"] = options["seed"]
        return await self._run_job(job_input)

    async def edit_image(self, image_bytes: bytes, prompt: str, **options) -> bytes:
        if not image_bytes:
            raise ImageBackendError("No input image provided for editing")
        job_input = {
            "task": "edit_lora" if options.get("lora") else "edit",
            "prompt": prompt,
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "steps": options.get("steps", settings.QWEN_EDIT_STEPS),
        }
        if options.get("seed") is not None:
            job_input["seed"] = options["seed"]
        return await self._run_job(job_input)

    async def _run_job(self, job_input: dict) -> bytes:
        if not settings.RUNPOD_API_KEY or not settings.RUNPOD_ENDPOINT_ID:
            raise ImageBackendError(
                "Image backend is not configured: set RUNPOD_API_KEY and "
                "RUNPOD_ENDPOINT_ID (or IMAGE_BACKEND=dummy for testing)."
            )

        endpoint = f"{settings.RUNPOD_API_BASE_URL}/v2/{settings.RUNPOD_ENDPOINT_ID}"
        headers = {"Authorization": f"Bearer {settings.RUNPOD_API_KEY}"}
        # One request at a time with headroom for the final poll after the
        # deadline check; the deadline itself is enforced below.
        timeout = aiohttp.ClientTimeout(total=settings.IMAGE_BACKEND_TIMEOUT_SECONDS + 30)

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            job_id = await self._submit(session, endpoint, job_input)
            return await self._wait_for_result(session, endpoint, job_id)

    async def _submit(
        self, session: aiohttp.ClientSession, endpoint: str, job_input: dict
    ) -> str:
        url = f"{endpoint}/run"
        try:
            async with session.post(url, json={"input": job_input}) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise ImageBackendError(
                        f"RunPod submit failed: HTTP {resp.status} {body}"
                    )
        except aiohttp.ClientError as e:
            raise ImageBackendError(f"RunPod submit request failed: {e}") from e

        job_id = body.get("id") if isinstance(body, dict) else None
        if not job_id:
            raise ImageBackendError(f"RunPod submit returned no job id: {body}")
        logger.info("Submitted RunPod job %s (task=%s)", job_id, job_input.get("task"))
        return job_id

    async def _wait_for_result(
        self, session: aiohttp.ClientSession, endpoint: str, job_id: str
    ) -> bytes:
        deadline = time.monotonic() + settings.IMAGE_BACKEND_TIMEOUT_SECONDS
        status_url = f"{endpoint}/status/{job_id}"

        while True:
            try:
                async with session.get(status_url) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise ImageBackendError(
                            f"RunPod status check failed: HTTP {resp.status} {body}"
                        )
            except aiohttp.ClientError as e:
                raise ImageBackendError(f"RunPod status request failed: {e}") from e

            status = (body.get("status") or "").upper() if isinstance(body, dict) else ""
            if status == "COMPLETED":
                return self._extract_image(body, job_id)
            if status in _TERMINAL_STATUSES:
                raise ImageBackendError(
                    f"RunPod job {job_id} ended as {status}: "
                    f"{body.get('error') if isinstance(body, dict) else body}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImageBackendError(
                    f"RunPod job {job_id} did not finish within "
                    f"{settings.IMAGE_BACKEND_TIMEOUT_SECONDS}s "
                    f"(last status: {status or 'UNKNOWN'})"
                )
            await asyncio.sleep(min(settings.IMAGE_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def _extract_image(body: dict, job_id: str) -> bytes:
        output = body.get("output")
        image_b64 = output.get("image_base64") if isinstance(output, dict) else None
        if not image_b64:
            raise ImageBackendError(
                f"RunPod job {job_id} completed without an image: "
                f"{body.get('error') or output}"
            )
        try:
            return base64.b64decode(image_b64)
        except (ValueError, TypeError) as e:
            raise ImageBackendError(f"RunPod job {job_id} returned invalid image data: {e}") from e
