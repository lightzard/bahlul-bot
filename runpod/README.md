# RunPod Serverless worker — Qwen Image 2.1 (GGUF) image backend

This directory contains the GPU backend for the bot's `/draw` and `/edit`
commands. The Vercel app never runs the model; it submits jobs to a RunPod
Serverless endpoint:

```
Telegram -> FastAPI/webhook (Vercel) -> api/image_backend (RunPod provider)
          -> RunPod Serverless endpoint (this worker)
          -> ComfyUI + UnetLoaderGGUF -> Qwen Image 2.1 GGUF
          -> PNG back to Telegram
```

The model files come from the public Hugging Face repo
[`KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF`](https://huggingface.co/KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF)
(duplicate of `abenzerps/Qwen-Image-2.1-Uncensored-GGUF`):

| Role | File | Size |
|---|---|---:|
| Diffusion model (GGUF) | `qwen-image-2.1-Q4_K_M.gguf` | 4.6 GiB |
| Text encoder | `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | 8.7 GiB |
| VAE | `vae/qwen_image_2.1_vae_bf16.safetensors` | 644 MiB |

They are downloaded **once** into a Network Volume and reused by every later
cold start — nothing is re-downloaded per request, and the weights are never
re-uploaded anywhere.

Runtime stack: **ComfyUI** (commit-pinned in the Dockerfile) +
**[leejet/ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF)**. The leejet
fork is required — the older city96 version fails on this model with
`Unknown model architecture!`.

## 1. Build and push the image

```bash
docker build -t <your-registry>/qwen-image-2.1-runpod:latest .
docker push <your-registry>/qwen-image-2.1-runpod:latest
```

Any registry RunPod can pull from works (Docker Hub, GHCR with a public repo,
etc.). The image contains only ComfyUI + code (~15 GB with the PyTorch base);
weights are not baked in.

## 2. Create a Network Volume

RunPod Console → **Storage → Network Volume** → create, **20 GB or larger**
(weights total ~14 GB). Attach it to the data center you will run the endpoint
in (e.g. `US-OR-1`). It is mounted at `/runpod-volume`, which the handler
uses as `MODEL_CACHE_DIR`.

Cost is ~$0.07/GB/month → about $1.5/month for 20 GB.

## 3. Create the Serverless endpoint

RunPod Console → **Serverless → New Endpoint**:

| Setting | Value | Why |
|---|---|---|
| GPU | RTX 4090 (24 GB) recommended; any 16 GB+ GPU works | Q4_K_M needs ~5 GB VRAM; the text encoder runs in system RAM |
| GPU Memory / System RAM | defaults fine, but ensure **16 GB+ system RAM** | int8 text encoder ~9 GB in RAM |
| Active Workers | **0** | scale to zero, no idle GPU cost |
| Max Workers | **1** | single-user bot; avoids double model loads |
| Idle Timeout | **300–600 s** (5–10 min) | warm reuse during short bursts |
| FlashBoot | optional | slightly faster cold starts |
| Network Volume | select the volume from step 2 | model cache |
| Container image | the image from step 1 | |

Optionally set worker env vars (all have sensible defaults):

| Env var | Default | Purpose |
|---|---|---|
| `HF_REPO_ID` | `KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF` | model source |
| `MODEL_GGUF` | `qwen-image-2.1-Q4_K_M.gguf` | pick another quant (e.g. `qwen-image-2.1-Q8_0.gguf`) |
| `MODEL_TEXT_ENCODER` | `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | encoder file |
| `MODEL_VAE` | `vae/qwen_image_2.1_vae_bf16.safetensors` | VAE file |
| `HF_REVISION` | *(latest)* | pin a repo revision |
| `MODEL_CACHE_DIR` | `/runpod-volume/qwen-image-2.1` | where weights live |
| `COMFYUI_LOWVRAM` | `0` | set `1` to pass `--lowvram` to ComfyUI |
| `COMFYUI_EXTRA_ARGS` | *(empty)* | extra ComfyUI CLI flags |
| `COMFYUI_JOB_TIMEOUT` | `600` | per-job hard timeout (s) |
| `HF_TOKEN` | *(none)* | repo is public; only needed if you fork it privately |

## 4. Test the endpoint

```bash
export RUNPOD_API_KEY=...   # RunPod Console → Settings → API Keys
export ENDPOINT_ID=...      # from the endpoint page

# Health check (also triggers the first-boot model download):
curl -s -X POST "https://api.runpod.ai/v2/$ENDPOINT_ID/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"task": "test"}}'
```

First job after deployment downloads ~14 GB from Hugging Face to the volume
(expect 5–15 min depending on bandwidth). Later cold starts only load the
models into VRAM/RAM (~1–3 min). Warm jobs at 1024×1024 / 25 steps typically
take ~30–90 s depending on GPU.

A real generation via the async API (same shape the bot uses):

```bash
JOB=$(curl -s -X POST "https://api.runpod.ai/v2/$ENDPOINT_ID/run" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"task": "text2img", "prompt": "a cat astronaut, photo"}}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['id'])")

curl -s "https://api.runpod.ai/v2/$ENDPOINT_ID/status/$JOB" \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
# poll until status == COMPLETED, then decode output.image_base64 to a PNG
```

## 5. Wire up the bot

Set these on Vercel (replacing the old `GROK_API_KEY` / `OPENAI_API_KEY`):

```
RUNPOD_API_KEY=<key>
RUNPOD_ENDPOINT_ID=<endpoint id>
# IMAGE_BACKEND=dummy   # optional: offline stub for testing
```

Also see the root `README.md` for the Vercel `maxDuration` note (300 s).

## Cost sketch

With Active Workers 0 / Max Workers 1 you pay:

- Network Volume: ~$1.5/month (20 GB).
- GPU time only when jobs run (community RTX 4090 ≈ $0.34–0.44/hr secure
  ≈ $0.69/hr). A warm 60 s job ≈ $0.01; a cold start adds ~$0.02–0.05.
- RunPod serverless has no per-request surcharge beyond GPU time.

## Troubleshooting

- **`Unknown model architecture!`** — you are on the old city96
  ComfyUI-GGUF. The image pins leejet's fork; make sure you built this
  Dockerfile and did not mount an older custom node.
- **CUDA out of memory** — set `COMFYUI_LOWVRAM=1`, or pick a bigger GPU.
  The text encoder already runs in system RAM (`CLIPLoader device=cpu`).
- **First job takes ~10 min** — that is the one-time weight download to the
  volume; trigger it once with the `test` task after deploying.
- **`COMFYUI did not become ready`** — check worker logs; usually a failed
  model download (retry) or an OOM during ComfyUI start on a tiny GPU.
- **Model file / node mismatches after upgrading ComfyUI** — the API
  workflows in `workflows/` were built from the official Comfy-Org
  Qwen-Image-2.1 templates (UI format, converted to API format, UNETLoader
  swapped for `UnetLoaderGGUF`). If a future ComfyUI release renames nodes,
  re-export the official template via *Save (API Format)* and re-swap the
  loader node.

## License note

The model carries the **Qwen Research License** and this particular build is
uncensored (no safety filter). Running it is your responsibility; keep the
bot's whitelist (`WHITELIST_IDS`) closed.
