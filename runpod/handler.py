"""RunPod Serverless worker: Qwen Image 2.1 GGUF on ComfyUI.

Serves the bot's image backend (see api/image_backend/runpod_backend.py).
Model files are downloaded once from Hugging Face into a cache dir — ideally
a RunPod Network Volume so later cold starts reuse them — and ComfyUI runs as
an in-container subprocess driven through its HTTP API.

Job input (JSON):
    task:           "text2img" | "edit" | "test"
    prompt:         str
    image_base64:   str   (edit only)
    width, height:  int   (text2img only, default 1024)
    steps:          int   (default 25, clamped to 1..50)
    seed:           int   (optional; random when omitted)

Job output:
    {"image_base64": "<png bytes base64>"} on success.
"""

import base64
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from huggingface_hub import hf_hub_download

import runpod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qwen-worker")

# ---------------------------------------------------------------------------
# Configuration (env)
# ---------------------------------------------------------------------------
HF_REPO_ID = os.getenv("HF_REPO_ID", "KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF")
HF_REVISION = os.getenv("HF_REVISION") or None
MODEL_GGUF = os.getenv("MODEL_GGUF", "qwen-image-2.1-Q4_K_M.gguf")
MODEL_TEXT_ENCODER = os.getenv("MODEL_TEXT_ENCODER", "text_encoders/qwen3vl_8b_int8_convrot.safetensors")
MODEL_VAE = os.getenv("MODEL_VAE", "vae/qwen_image_2.1_vae_bf16.safetensors")

# Optional LoRA on top of the diffusion model. MODEL_LORA is a path relative
# to MODEL_CACHE_DIR (uploaded manually to the volume, e.g. via a temporary
# pod). Empty or missing file = LoRA disabled. See runpod/README.md.
MODEL_LORA = os.getenv("MODEL_LORA", "").strip()
try:
    MODEL_LORA_STRENGTH = float(os.getenv("MODEL_LORA_STRENGTH", "1.0"))
except ValueError:
    MODEL_LORA_STRENGTH = 1.0

_VOLUME = os.getenv("RUNPOD_VOLUME_PATH", "/runpod-volume")
_DEFAULT_CACHE = (
    f"{_VOLUME}/qwen-image-2.1" if os.path.isdir(_VOLUME) else "/local-models/qwen-image-2.1"
)
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", _DEFAULT_CACHE)

COMFYUI_DIR = os.getenv("COMFYUI_DIR", "/ComfyUI")
COMFYUI_HOST = "127.0.0.1"
COMFYUI_PORT = int(os.getenv("COMFYUI_PORT", "8188"))
COMFYUI_URL = f"http://{COMFYUI_HOST}:{COMFYUI_PORT}"
COMFYUI_LOWVRAM = os.getenv("COMFYUI_LOWVRAM", "") == "1"
COMFYUI_EXTRA_ARGS = os.getenv("COMFYUI_EXTRA_ARGS", "")
COMFYUI_STARTUP_TIMEOUT = int(os.getenv("COMFYUI_STARTUP_TIMEOUT", "300"))
COMFYUI_JOB_TIMEOUT = int(os.getenv("COMFYUI_JOB_TIMEOUT", "600"))

WORKFLOW_DIR = Path(os.getenv("WORKFLOW_DIR", Path(__file__).parent / "workflows"))

DEFAULT_STEPS = 25
MAX_STEPS = 50
MAX_LORA_BYTES = 5 * 1024**3

# Marker file on the volume: the LoRA selected by the last fetch_lora job,
# kept across cold starts so the endpoint needs no env change. MODEL_LORA
# (env) takes precedence when both are set.
_LORA_MARKER_NAME = "lora.txt"

_DRIVE_ID_PATTERNS = [
    re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/(?:open|uc)\?[^#]*id=([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.usercontent\.google\.com/download\?[^#]*id=([A-Za-z0-9_-]+)"),
]

_comfyui_process = None
# Basename of the active LoRA file, or None when disabled. Set by
# _prepare_lora() at bootstrap.
_lora_name = None


def _direct_download_url(url: str) -> str:
    """Normalize Google Drive share links to a direct-download URL."""
    for pattern in _DRIVE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return (
                "https://drive.usercontent.google.com/download"
                f"?id={match.group(1)}&export=download&confirm=t"
            )
    return url


def _filename_from_url(url: str) -> str:
    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    if not name or name.lower() in {"view", "download", "open", "uc", "get"}:
        return "qwen-lora"
    return name


def _looks_like_safetensors(prefix: bytes) -> bool:
    """safetensors files start with an 8-byte LE header length + JSON."""
    if len(prefix) < 9 or prefix[8:9] != b"{":
        return False
    header_len = int.from_bytes(prefix[:8], "little")
    return 2 <= header_len <= 100_000_000


# ---------------------------------------------------------------------------
# Bootstrap: models + ComfyUI subprocess
# ---------------------------------------------------------------------------
def _link_model(target: Path, comfy_subdir: str, *, fallback_copy: bool) -> None:
    """Expose a cached model file to ComfyUI's model folders via symlink."""
    link = Path(COMFYUI_DIR) / "models" / comfy_subdir / target.name
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        return
    try:
        link.symlink_to(target.resolve())
        logger.info("Linked %s -> %s", link, target)
    except OSError as e:
        if not fallback_copy:
            # Copying multi-GB weights onto the container disk would blow it.
            raise RuntimeError(f"Cannot expose {target} to ComfyUI via {link}: {e}") from e
        logger.warning("Symlink unavailable (%s); copying %s", e, target)
        shutil.copy2(target, link)


def _download_models() -> None:
    """Fetch the three model files from HF once; no-op when already cached."""
    files = {
        MODEL_GGUF: "diffusion_models",
        MODEL_TEXT_ENCODER: "text_encoders",
        MODEL_VAE: "vae",
    }
    for repo_file, comfy_dir in files.items():
        target = Path(MODEL_CACHE_DIR) / repo_file
        if target.exists():
            logger.info("Model file already cached: %s", target)
        else:
            logger.info("Downloading %s from %s ...", repo_file, HF_REPO_ID)
            hf_hub_download(
                repo_id=HF_REPO_ID,
                filename=repo_file,
                revision=HF_REVISION,
                local_dir=MODEL_CACHE_DIR,
            )
        _link_model(target, comfy_dir, fallback_copy=False)


def _prepare_lora() -> None:
    """Resolve the optional LoRA: no download, just verify + symlink.

    Precedence: MODEL_LORA env > lora.txt marker on the volume (written by
    the fetch_lora task) > disabled. A configured-but-missing LoRA logs a
    loud warning instead of crashing the worker.
    """
    global _lora_name
    marker = Path(MODEL_CACHE_DIR) / _LORA_MARKER_NAME
    if MODEL_LORA:
        candidate = MODEL_LORA
    elif marker.exists():
        candidate = marker.read_text(encoding="utf-8").strip()
        logger.info("LoRA selected by volume marker (%s): %s", _LORA_MARKER_NAME, candidate)
    else:
        return
    target = Path(MODEL_CACHE_DIR) / candidate
    if not target.exists():
        logger.warning(
            "LoRA '%s' (%s) does not exist on the volume — LoRA disabled. "
            "Upload it via the fetch_lora task (see runpod/README.md).",
            candidate,
            target,
        )
        return
    _link_model(target, "loras", fallback_copy=True)
    _lora_name = target.name
    logger.info("LoRA active: %s (strength %.2f)", _lora_name, MODEL_LORA_STRENGTH)


def _fetch_lora(job_input: dict) -> dict:
    """Download a LoRA from a URL (Google Drive share links supported)."""
    url = str(job_input.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("fetch_lora requires an 'url' (http/https)")
    filename = str(job_input.get("filename") or "").strip() or _filename_from_url(url)
    if not filename.endswith(".safetensors"):
        filename += ".safetensors"

    direct = _direct_download_url(url)
    logger.info("Fetching LoRA from %s", direct)
    tmp = Path(MODEL_CACHE_DIR) / f".lora-download-{int(time.time() * 1000)}"
    size = 0
    try:
        with requests.get(direct, stream=True, timeout=(15, 600), allow_redirects=True) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"Download failed: HTTP {resp.status_code}")
            if "text/html" in (resp.headers.get("Content-Type") or "").lower():
                raise RuntimeError(
                    "The URL returned an HTML page instead of the file. For Google "
                    "Drive: Share -> 'Anyone with the link' and set the role to "
                    "'Anyone on the internet'."
                )
            checked_prefix = False
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not checked_prefix:
                        checked_prefix = True
                        if not _looks_like_safetensors(chunk[:64]):
                            raise RuntimeError(
                                "The URL did not return a safetensors file "
                                "(unexpected content)."
                            )
                    size += len(chunk)
                    if size > MAX_LORA_BYTES:
                        raise RuntimeError(f"LoRA exceeds the {MAX_LORA_BYTES} byte cap")
                    fh.write(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    target = Path(MODEL_CACHE_DIR) / filename
    os.replace(tmp, target)
    (Path(MODEL_CACHE_DIR) / _LORA_MARKER_NAME).write_text(filename, encoding="utf-8")

    global _lora_name
    _link_model(target, "loras", fallback_copy=True)
    _lora_name = filename
    logger.info("LoRA fetched and active: %s (%d bytes)", filename, size)
    return {"ok": True, "filename": filename, "size_bytes": size}


def _lora_off() -> dict:
    global _lora_name
    (Path(MODEL_CACHE_DIR) / _LORA_MARKER_NAME).unlink(missing_ok=True)
    _lora_name = None
    message = "LoRA marker removed; LoRA disabled on this worker"
    if MODEL_LORA:
        message += (
            f" — but MODEL_LORA env is set to {MODEL_LORA!r} and will re-enable it "
            "on the next worker; clear that env var on the endpoint to disable fully"
        )
    logger.info(message)
    return {"ok": True, "detail": message}


def _start_comfyui() -> None:
    global _comfyui_process
    args = [
        sys.executable,
        "main.py",
        "--listen",
        COMFYUI_HOST,
        "--port",
        str(COMFYUI_PORT),
        "--disable-auto-launch",
        "--disable-metadata",
    ]
    if COMFYUI_LOWVRAM:
        args.append("--lowvram")
    if COMFYUI_EXTRA_ARGS:
        args.extend(COMFYUI_EXTRA_ARGS.split())

    logger.info("Starting ComfyUI: %s", " ".join(args))
    _comfyui_process = subprocess.Popen(args, cwd=COMFYUI_DIR)

    deadline = time.time() + COMFYUI_STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            if requests.get(f"{COMFYUI_URL}/system_stats", timeout=5).status_code == 200:
                logger.info("ComfyUI is ready on %s", COMFYUI_URL)
                return
        except requests.RequestException:
            pass
        if _comfyui_process.poll() is not None:
            raise RuntimeError(f"ComfyUI exited during startup with code {_comfyui_process.returncode}")
        time.sleep(2)
    raise RuntimeError(f"ComfyUI did not become ready within {COMFYUI_STARTUP_TIMEOUT}s")


def bootstrap() -> None:
    _download_models()
    _prepare_lora()
    _start_comfyui()


# ---------------------------------------------------------------------------
# Workflow building
# ---------------------------------------------------------------------------
def _load_template(name: str) -> dict:
    with open(WORKFLOW_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _find_node(workflow: dict, class_type: str):
    """Return (node_id, node) of the single node with the given class_type."""
    matches = [(k, v) for k, v in workflow.items() if isinstance(v, dict) and v.get("class_type") == class_type]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {class_type} node in workflow, found {len(matches)}")
    return matches[0]


def _set_model_files(workflow: dict) -> None:
    _find_node(workflow, "UnetLoaderGGUF")[1]["inputs"]["unet_name"] = Path(MODEL_GGUF).name
    _find_node(workflow, "CLIPLoader")[1]["inputs"]["clip_name"] = Path(MODEL_TEXT_ENCODER).name
    _find_node(workflow, "VAELoader")[1]["inputs"]["vae_name"] = Path(MODEL_VAE).name
    _apply_lora(workflow)


def _apply_lora(workflow: dict) -> None:
    """Patch or bypass the LoraLoaderModelOnly node based on bootstrap state."""
    lora_ids = [
        nid for nid, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "LoraLoaderModelOnly"
    ]
    if _lora_name:
        for nid in lora_ids:
            inputs = workflow[nid]["inputs"]
            inputs["lora_name"] = _lora_name
            inputs["strength_model"] = MODEL_LORA_STRENGTH
        return
    # Disabled: drop the node and rewire its consumers straight to the loader.
    loader_id = _find_node(workflow, "UnetLoaderGGUF")[0]
    for nid in lora_ids:
        bypassed = [nid, 0]
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            for name, value in node["inputs"].items():
                if value == bypassed:
                    node["inputs"][name] = [loader_id, 0]
        workflow.pop(nid, None)


def _build_text2img(job_input: dict) -> dict:
    workflow = _load_template("text2img_api.json")
    _set_model_files(workflow)

    width = int(job_input.get("width") or 1024)
    height = int(job_input.get("height") or 1024)
    latent = _find_node(workflow, "EmptyLatentImage")[1]["inputs"]
    latent["width"] = max(256, min(2048, round(width / 32) * 32))
    latent["height"] = max(256, min(2048, round(height / 32) * 32))

    encode = _find_node(workflow, "TextEncodeQwenImage21")[1]["inputs"]
    encode["prompt"] = str(job_input.get("prompt") or "")

    sampler = _find_node(workflow, "KSampler")[1]["inputs"]
    sampler["steps"] = max(1, min(MAX_STEPS, int(job_input.get("steps") or DEFAULT_STEPS)))
    sampler["seed"] = int(job_input.get("seed") if job_input.get("seed") is not None else random.randint(0, 2**53))
    return workflow


def _build_edit(job_input: dict, job_id: str) -> tuple[dict, str]:
    image_b64 = job_input.get("image_base64")
    if not image_b64:
        raise ValueError("task 'edit' requires 'image_base64'")
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except (ValueError, TypeError) as e:
        raise ValueError(f"invalid image_base64: {e}") from e
    if len(image_bytes) > 20 * 1024 * 1024:
        raise ValueError("input image larger than 20 MB")

    input_dir = Path(COMFYUI_DIR) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    filename = f"rp_{job_id}.jpg"
    (input_dir / filename).write_bytes(image_bytes)

    workflow = _load_template("edit_api.json")
    _set_model_files(workflow)
    _find_node(workflow, "LoadImage")[1]["inputs"]["image"] = filename

    encode = _find_node(workflow, "TextEncodeQwenImage21")[1]["inputs"]
    encode["prompt"] = str(job_input.get("prompt") or "")

    sampler = _find_node(workflow, "KSampler")[1]["inputs"]
    sampler["steps"] = max(1, min(MAX_STEPS, int(job_input.get("steps") or DEFAULT_STEPS)))
    sampler["seed"] = int(job_input.get("seed") if job_input.get("seed") is not None else random.randint(0, 2**53))
    return workflow, filename


# ---------------------------------------------------------------------------
# ComfyUI API
# ---------------------------------------------------------------------------
def _submit_workflow(workflow: dict) -> str:
    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow}, timeout=30)
    body = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI rejected the workflow: {body}")
    prompt_id = body.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return a prompt_id: {body}")
    logger.info("Submitted ComfyUI prompt %s", prompt_id)
    return prompt_id


def _wait_for_history(prompt_id: str) -> dict:
    deadline = time.time() + COMFYUI_JOB_TIMEOUT
    while time.time() < deadline:
        try:
            hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=15).json()
        except requests.RequestException:
            hist = {}
        entry = hist.get(prompt_id)
        if entry:
            return entry
        time.sleep(2)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish within {COMFYUI_JOB_TIMEOUT}s")


def _raise_if_error(history_entry: dict) -> None:
    status = history_entry.get("status", {})
    if status.get("status_str") == "error":
        for message in status.get("messages", []):
            if message and message[0] == "execution_error":
                err = message[1]
                raise RuntimeError(
                    f"ComfyUI execution error in node {err.get('node_type')}: "
                    f"{err.get('exception_message')}"
                )
        raise RuntimeError(f"ComfyUI execution error: {status.get('messages')}")


def _fetch_output_image(history_entry: dict) -> bytes:
    for node_output in history_entry.get("outputs", {}).values():
        for image in node_output.get("images", []):
            if image.get("type") != "output":
                continue
            resp = requests.get(
                f"{COMFYUI_URL}/view",
                params={
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": "output",
                },
                timeout=120,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch output image: HTTP {resp.status_code}")
            return resp.content
    raise RuntimeError("ComfyUI workflow finished without an output image")


# ---------------------------------------------------------------------------
# RunPod entrypoint
# ---------------------------------------------------------------------------
def handler(job):
    job_id = job["id"]
    job_input = job.get("input") or {}
    task = job_input.get("task")
    started = time.time()
    input_file = None

    try:
        if task == "test":
            stats = requests.get(f"{COMFYUI_URL}/system_stats", timeout=15).json()
            return {
                "ok": True,
                "comfyui_url": COMFYUI_URL,
                "repo": HF_REPO_ID,
                "models": [MODEL_GGUF, MODEL_TEXT_ENCODER, MODEL_VAE],
                "lora": _lora_name or "none",
                "lora_strength": MODEL_LORA_STRENGTH if _lora_name else None,
                "cache_dir": MODEL_CACHE_DIR,
                "system": stats.get("system"),
            }

        if task == "fetch_lora":
            return _fetch_lora(job_input)

        if task == "lora_off":
            return _lora_off()

        if task == "text2img":
            workflow = _build_text2img(job_input)
        elif task == "edit":
            workflow, input_file = _build_edit(job_input, job_id)
        else:
            raise ValueError(
                f"unknown task: {task!r} "
                "(expected text2img, edit, test, fetch_lora, or lora_off)"
            )

        prompt_id = _submit_workflow(workflow)
        history_entry = _wait_for_history(prompt_id)
        _raise_if_error(history_entry)
        image_bytes = _fetch_output_image(history_entry)

        logger.info(
            "Job %s (%s) produced %d image bytes in %.1fs",
            job_id, task, len(image_bytes), time.time() - started,
        )
        return {"image_base64": base64.b64encode(image_bytes).decode("ascii")}
    except Exception as e:
        logger.error("Job %s failed: %s", job_id, e)
        return {"error": str(e)}
    finally:
        if input_file:
            try:
                (Path(COMFYUI_DIR) / "input" / input_file).unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    bootstrap()
    runpod.serverless.start({"handler": handler})
