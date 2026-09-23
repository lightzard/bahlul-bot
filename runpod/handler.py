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
import subprocess
import sys
import time
from pathlib import Path

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

_comfyui_process = None


# ---------------------------------------------------------------------------
# Bootstrap: models + ComfyUI subprocess
# ---------------------------------------------------------------------------
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
        # Make the file visible to ComfyUI's model folders (works across
        # container restarts because MODEL_CACHE_DIR lives on the volume).
        link = Path(COMFYUI_DIR) / "models" / comfy_dir / target.name
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(target.resolve())
            logger.info("Linked %s -> %s", link, target)


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
                "cache_dir": MODEL_CACHE_DIR,
                "system": stats.get("system"),
            }

        if task == "text2img":
            workflow = _build_text2img(job_input)
        elif task == "edit":
            workflow, input_file = _build_edit(job_input, job_id)
        else:
            raise ValueError(f"unknown task: {task!r} (expected text2img, edit, or test)")

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
