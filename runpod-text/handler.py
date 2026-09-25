"""RunPod Serverless worker: HauhauCS Qwen3.5-4B Uncensored on llama.cpp.

Serves the bot's uncensored text backend (see api/text_backend.py). The GGUF
is downloaded once from Hugging Face into a cache dir — ideally a RunPod
Network Volume shared with the image worker — and llama-server runs as an
in-container subprocess driven through its OpenAI-compatible HTTP API.

Job input (JSON):
    task:        "generate" | "test"
    messages:    list of {"role": "system"|"user"|"assistant", "content": str}
    max_tokens:  int    (default 1024, clamped 1..4096)
    temperature: float  (default 0.7, clamped 0.0..2.0)

Job output:
    {"content": "<reply text>"} on success.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path

import requests
from huggingface_hub import hf_hub_download

import runpod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hauhaucs-text-worker")

# ---------------------------------------------------------------------------
# Configuration (env)
# ---------------------------------------------------------------------------
HF_REPO_ID = os.getenv("HF_REPO_ID", "HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive")
HF_REVISION = os.getenv("HF_REVISION") or None
# Default quant per the model card's own examples. Q6_K / Q8_0 from the same
# repo are drop-in upgrades if the 4B answers feel weak.
MODEL_GGUF = os.getenv("MODEL_GGUF", "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf")
HF_TOKEN = os.getenv("HF_TOKEN") or None

_VOLUME = os.getenv("RUNPOD_VOLUME_PATH", "/runpod-volume")
_DEFAULT_CACHE = (
    f"{_VOLUME}/hauhaucs-text" if os.path.isdir(_VOLUME) else "/local-models/hauhaucs-text"
)
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", _DEFAULT_CACHE)

LLAMA_SERVER_BIN = os.getenv("LLAMA_SERVER_BIN", "/app/llama-server")
LLAMA_HOST = "127.0.0.1"
LLAMA_PORT = int(os.getenv("LLAMA_PORT", "8080"))
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
CTX_SIZE = int(os.getenv("CTX_SIZE", "16384"))
GPU_LAYERS = os.getenv("GPU_LAYERS", "99")
LLAMA_EXTRA_ARGS = os.getenv("LLAMA_EXTRA_ARGS", "")
LLAMA_STARTUP_TIMEOUT = int(os.getenv("LLAMA_STARTUP_TIMEOUT", "300"))
LLAMA_JOB_TIMEOUT = int(os.getenv("LLAMA_JOB_TIMEOUT", "280"))
LLAMA_LOG_PATH = os.getenv("LLAMA_LOG_PATH", "/tmp/llama-server.log")

# Non-thinking sampler preset from the model card (attributed to the Qwen
# authors); generation requests may override temperature only.
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.8
DEFAULT_TOP_K = 20
DEFAULT_MIN_P = 0.0
DEFAULT_MAX_TOKENS = 1024
MAX_MAX_TOKENS = 4096

_llama_process = None

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_reasoning(text: str) -> str:
    """Drop thinking blocks; the chat preset disables thinking but templates
    and older builds can still emit them."""
    text = _THINK_BLOCK_RE.sub("", text)
    # An unterminated <think> (stream cut mid-block) leaves reasoning open.
    open_tag = text.find("<think>")
    if open_tag != -1:
        text = text[:open_tag]
    return text.strip()


# ---------------------------------------------------------------------------
# Bootstrap: model download + llama-server subprocess
# ---------------------------------------------------------------------------
def _download_model() -> Path:
    """Fetch the GGUF from HF once; no-op when already cached."""
    target = Path(MODEL_CACHE_DIR) / MODEL_GGUF
    if target.exists():
        logger.info("Model file already cached: %s", target)
        return target
    logger.info("Downloading %s from %s ...", MODEL_GGUF, HF_REPO_ID)
    hf_hub_download(
        repo_id=HF_REPO_ID,
        filename=MODEL_GGUF,
        revision=HF_REVISION,
        local_dir=MODEL_CACHE_DIR,
        token=HF_TOKEN,
    )
    return target


def _start_llama_server(model_path: Path) -> None:
    global _llama_process
    args = [
        LLAMA_SERVER_BIN,
        "-m",
        str(model_path),
        "--host",
        LLAMA_HOST,
        "--port",
        str(LLAMA_PORT),
        "-ngl",
        GPU_LAYERS,
        "-c",
        str(CTX_SIZE),
        # Use the chat template embedded in the GGUF metadata.
        "--jinja",
    ]
    if LLAMA_EXTRA_ARGS:
        args.extend(LLAMA_EXTRA_ARGS.split())

    logger.info("Starting llama-server: %s", " ".join(args))
    log_file = open(LLAMA_LOG_PATH, "ab")
    _llama_process = subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT)

    deadline = time.time() + LLAMA_STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            if requests.get(f"{LLAMA_URL}/health", timeout=5).status_code == 200:
                logger.info("llama-server is ready on %s", LLAMA_URL)
                return
        except requests.RequestException:
            pass
        if _llama_process.poll() is not None:
            _log_llama_tail()
            raise RuntimeError(
                f"llama-server exited during startup with code "
                f"{_llama_process.returncode} — check {LLAMA_LOG_PATH}; an "
                "unknown-architecture error means the image's llama.cpp "
                "version (LLAMA_CPP_VERSION in runpod-text/Dockerfile) "
                "predates this model — bump it to a newer release tag and "
                "rebuild"
            )
        time.sleep(2)
    _log_llama_tail()
    raise RuntimeError(f"llama-server did not become ready within {LLAMA_STARTUP_TIMEOUT}s")


def _log_llama_tail() -> None:
    """Echo the last server log lines so cold-start failures are visible in
    the RunPod worker logs."""
    try:
        lines = Path(LLAMA_LOG_PATH).read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-30:]:
            logger.info("llama-server: %s", line)
    except OSError:
        pass


def bootstrap() -> None:
    model = _download_model()
    _start_llama_server(model)


# ---------------------------------------------------------------------------
# Task handlers
# ---------------------------------------------------------------------------
def _generate(job_input: dict) -> dict:
    messages = job_input.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("task 'generate' requires a non-empty 'messages' list")
    clean = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") not in ("system", "user", "assistant"):
            raise ValueError(f"invalid message entry: {msg!r}")
        content = msg.get("content")
        if not isinstance(content, str):
            raise ValueError(f"invalid message content: {msg!r}")
        clean.append({"role": msg["role"], "content": content})

    try:
        max_tokens = int(job_input.get("max_tokens") or DEFAULT_MAX_TOKENS)
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    max_tokens = max(1, min(MAX_MAX_TOKENS, max_tokens))

    try:
        temperature = float(job_input.get("temperature", DEFAULT_TEMPERATURE))
    except (TypeError, ValueError):
        temperature = DEFAULT_TEMPERATURE
    temperature = max(0.0, min(2.0, temperature))

    payload = {
        "messages": clean,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": DEFAULT_TOP_P,
        "top_k": DEFAULT_TOP_K,
        "min_p": DEFAULT_MIN_P,
        "stream": False,
        # Ask the embedded template to disable thinking; templates without the
        # toggle ignore it, and _strip_reasoning covers the rest.
        "chat_template_kwargs": {"enable_thinking": False},
    }

    def post(payload: dict) -> requests.Response:
        return requests.post(
            f"{LLAMA_URL}/v1/chat/completions", json=payload, timeout=LLAMA_JOB_TIMEOUT
        )

    try:
        resp = post(payload)
    except requests.RequestException as e:
        raise RuntimeError(f"llama-server request failed: {e}") from e
    if resp.status_code == 400 and "chat_template_kwargs" in payload:
        # Older server builds reject unknown request fields — retry without.
        payload.pop("chat_template_kwargs")
        resp = post(payload)
    if resp.status_code != 200:
        raise RuntimeError(f"llama-server returned HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"llama-server returned no choices: {data}")
    content = _strip_reasoning((choices[0].get("message") or {}).get("content") or "")
    usage = data.get("usage") or {}
    logger.info(
        "Completion tokens: prompt=%s completion=%s total=%s",
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    if not content:
        raise RuntimeError("llama-server returned an empty completion")
    return {"content": content}


def _test() -> dict:
    health = requests.get(f"{LLAMA_URL}/health", timeout=15).json()
    props = {}
    try:
        props = requests.get(f"{LLAMA_URL}/props", timeout=15).json()
    except requests.RequestException:
        pass
    return {
        "ok": True,
        "health": health,
        "llama_build": props.get("build_info") or props.get("build"),
        "repo": HF_REPO_ID,
        "model": MODEL_GGUF,
        "cache_dir": MODEL_CACHE_DIR,
        "context_size": CTX_SIZE,
    }


# ---------------------------------------------------------------------------
# RunPod entrypoint
# ---------------------------------------------------------------------------
def handler(job):
    job_id = job["id"]
    job_input = job.get("input") or {}
    task = job_input.get("task")
    started = time.time()

    try:
        if task == "test":
            return _test()
        if task == "generate":
            result = _generate(job_input)
            logger.info("Job %s (generate) finished in %.1fs", job_id, time.time() - started)
            return result
        raise ValueError(f"unknown task: {task!r} (expected generate or test)")
    except Exception as e:
        logger.error("Job %s failed: %s", job_id, e)
        return {"error": str(e)}


if __name__ == "__main__":
    bootstrap()
    runpod.serverless.start({"handler": handler})
