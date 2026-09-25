"""Uncensored text-generation backend abstraction.

The /nsfw command handlers depend only on generate_text() from this module;
the concrete runtime is selected via settings.TEXT_BACKEND. Today the real
backend is a RunPod Serverless endpoint running llama.cpp with the HauhauCS
Qwen3.5-4B Uncensored GGUF (see runpod-text/ in the repo root).
"""

import asyncio
import logging
import time

import aiohttp

from api import settings

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"FAILED", "CANCELLED", "TIMED_OUT"}


class TextBackendError(Exception):
    """Raised when the text backend fails, times out, or is misconfigured."""


class DummyTextBackend:
    """Offline stub for tests and local development. Select with
    TEXT_BACKEND=dummy."""

    async def generate_text(self, messages: list, **options) -> str:
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        return f"[dummy-nsfw] {last_user}"


class RunPodTextBackend:
    """Text backend driven by the RunPod Serverless REST API."""

    async def generate_text(self, messages: list, **options) -> str:
        job_input = {
            "task": "generate",
            "messages": [
                {"role": m["role"], "content": m["content"]} for m in messages
            ],
            "max_tokens": options.get("max_tokens", settings.NSFW_MAX_TOKENS),
            "temperature": options.get("temperature", settings.NSFW_TEMPERATURE),
        }
        return await self._run_job(job_input)

    async def _run_job(self, job_input: dict) -> str:
        if not settings.RUNPOD_API_KEY or not settings.RUNPOD_TEXT_ENDPOINT_ID:
            raise TextBackendError(
                "Text backend is not configured: set RUNPOD_API_KEY and "
                "RUNPOD_TEXT_ENDPOINT_ID (or TEXT_BACKEND=dummy for testing)."
            )

        endpoint = f"{settings.RUNPOD_API_BASE_URL}/v2/{settings.RUNPOD_TEXT_ENDPOINT_ID}"
        headers = {"Authorization": f"Bearer {settings.RUNPOD_API_KEY}"}
        # One request at a time with headroom for the final poll after the
        # deadline check; the deadline itself is enforced below.
        timeout = aiohttp.ClientTimeout(total=settings.NSFW_JOB_TIMEOUT_SECONDS + 30)

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
                    raise TextBackendError(
                        f"RunPod submit failed: HTTP {resp.status} {body}"
                    )
        except aiohttp.ClientError as e:
            raise TextBackendError(f"RunPod submit request failed: {e}") from e

        job_id = body.get("id") if isinstance(body, dict) else None
        if not job_id:
            raise TextBackendError(f"RunPod submit returned no job id: {body}")
        logger.info("Submitted RunPod text job %s (task=%s)", job_id, job_input.get("task"))
        return job_id

    async def _wait_for_result(
        self, session: aiohttp.ClientSession, endpoint: str, job_id: str
    ) -> str:
        deadline = time.monotonic() + settings.NSFW_JOB_TIMEOUT_SECONDS
        status_url = f"{endpoint}/status/{job_id}"

        while True:
            try:
                async with session.get(status_url) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise TextBackendError(
                            f"RunPod status check failed: HTTP {resp.status} {body}"
                        )
            except aiohttp.ClientError as e:
                raise TextBackendError(f"RunPod status request failed: {e}") from e

            status = (body.get("status") or "").upper() if isinstance(body, dict) else ""
            if status == "COMPLETED":
                return self._extract_content(body, job_id)
            if status in _TERMINAL_STATUSES:
                raise TextBackendError(
                    f"RunPod job {job_id} ended as {status}: "
                    f"{body.get('error') if isinstance(body, dict) else body}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TextBackendError(
                    f"RunPod job {job_id} did not finish within "
                    f"{settings.NSFW_JOB_TIMEOUT_SECONDS}s "
                    f"(last status: {status or 'UNKNOWN'})"
                )
            await asyncio.sleep(min(settings.NSFW_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def _extract_content(body: dict, job_id: str) -> str:
        output = body.get("output")
        content = output.get("content") if isinstance(output, dict) else None
        if not content:
            raise TextBackendError(
                f"RunPod job {job_id} completed without text: "
                f"{body.get('error') or output}"
            )
        return content


def get_text_backend():
    """Return the text backend selected by settings.TEXT_BACKEND."""
    name = (settings.TEXT_BACKEND or "").strip().lower()
    if name == "runpod":
        return RunPodTextBackend()
    if name == "dummy":
        return DummyTextBackend()
    raise TextBackendError(f"Unknown TEXT_BACKEND: {settings.TEXT_BACKEND!r}")


async def generate_text(messages: list, **options) -> str:
    """Generate uncensored text for the given chat messages."""
    backend = get_text_backend()
    return await backend.generate_text(messages, **options)
