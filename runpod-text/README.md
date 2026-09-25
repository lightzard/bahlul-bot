# RunPod Serverless worker — HauhauCS Qwen3.5-4B (GGUF) text backend

This directory contains the GPU backend for the bot's `/nsfw` (`/n`) command.
The Vercel app never runs the model; it submits jobs to a RunPod Serverless
endpoint, separate from (and independent of) the image worker in `runpod/`:

```
Telegram -> FastAPI/webhook (Vercel) -> api/text_backend (RunPod provider)
          -> RunPod Serverless endpoint (this worker)
          -> llama.cpp llama-server + HauhauCS Qwen3.5-4B GGUF
          -> reply text back to Telegram
```

The model comes from the public Hugging Face repo
[`HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive`](https://huggingface.co/HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive)
(0/465 refusals per the card):

| File | Size | Notes |
|---|---:|---|
| `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | 2.7 GiB | default (the card's own examples use it) |
| `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf` | 3.2 GiB | quality bump, drop-in swap |
| `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf` | 4.2 GiB | max quality, drop-in swap |
| `mmproj-...-BF16.gguf` | 0.6 GiB | vision projector — not used, text only |

It is downloaded **once** into the Network Volume (shared with the image
worker — attach the **same** volume) and reused by every later cold start.

Runtime stack: **[llama.cpp](https://github.com/ggml-org/llama.cpp)**
compiled from source in the Dockerfile (release tag pinned via
`LLAMA_CPP_VERSION`). A source build is required: the official GHCR
`server-cuda` images stopped publishing in early 2025 and predate the
Qwen3.5 ("qwen35") architecture. llama-server runs with the chat template
embedded in the GGUF (`--jinja`); generation uses the model card's
non-thinking sampler preset (temp 0.7, top_p 0.8, top_k 20, min_p 0);
`<think>` blocks are stripped defensively server-side.

The build compiles CUDA kernels for **architecture 89 (RTX 4090)** only —
change `CUDA_ARCHITECTURES` in the Dockerfile (e.g. `80;86;89`) if you plan
to run on other GPU classes. First build takes ~30–45 min on a small
builder; retries resume from the compiled objects via a BuildKit cache
mount, so fix-and-retry cycles only re-link. Two requirements learned the
hard way: the final link needs the CUDA **driver stubs**
(`-L/usr/local/cuda/lib64/stubs -lcuda` — the real driver only exists at
runtime), and shared libs (`BUILD_SHARED_LIBS=ON`) plus `-j 2` keep the
memory peak under ~5 GB — a single giant static link OOMs small builder VMs.

## 1. Build and push the image

```bash
docker build -t <your-registry>/hauhaucs-text-runpod:v0.4 -t <your-registry>/hauhaucs-text-runpod:latest ./runpod-text
docker push <your-registry>/hauhaucs-text-runpod:v0.4
docker push <your-registry>/hauhaucs-text-runpod:latest
```

Keep the endpoint pinned to the versioned tag (below) — `latest` is a
convenience for browsing; pinning prevents a future release from silently
changing what the endpoint runs.

Any registry RunPod can pull from works. The image contains only
llama-server + Python (~6 GB); weights are not baked in.

## 2. Create the Serverless endpoint

RunPod Console → **Serverless → New Endpoint**:

| Setting | Value | Why |
|---|---|---|
| GPU | RTX 4090 (24 GB) recommended; any 16 GB+ GPU works | Q4_K_M needs ~3 GB VRAM + KV cache |
| Active Workers | **0** | scale to zero, no idle GPU cost |
| Max Workers | **1** | single-user bot; keeps /nsfw replies ordered |
| Idle Timeout | **300–600 s** (5–10 min) | warm reuse during a chat session |
| Network Volume | **select the same volume as the image endpoint** | shared model cache (~24 GB total used of 30 GB) |
| Container image | the image from step 1 | |

The volume must be in the same data center as the image endpoint's to be
attachable to both (e.g. `US-OR-1`).

Optionally set worker env vars (all have sensible defaults):

| Env var | Default | Purpose |
|---|---|---|
| `HF_REPO_ID` | `HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive` | model source |
| `MODEL_GGUF` | `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | pick another quant (e.g. `...-Q8_0.gguf`) |
| `CTX_SIZE` | `16384` | context window; raise/lower before changing quant |
| `GPU_LAYERS` | `99` | layers offloaded to VRAM |
| `LLAMA_EXTRA_ARGS` | *(empty)* | extra `llama-server` CLI flags |
| `LLAMA_JOB_TIMEOUT` | `280` | per-request timeout (s); stays under the bot's budget |
| `HF_REVISION` | *(latest)* | pin a repo revision |
| `HF_TOKEN` | *(none)* | repo is public; only for private forks |
| `MODEL_CACHE_DIR` | `/runpod-volume/hauhaucs-text` | where the GGUF lives |

## 3. Test the endpoint

```bash
export RUNPOD_API_KEY=...        # same key as the image endpoint
export TEXT_ENDPOINT_ID=...      # from the endpoint page

# Health check (also triggers the first-boot model download, ~2.7 GB):
curl -s -X POST "https://api.runpod.ai/v2/$TEXT_ENDPOINT_ID/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"task": "test"}}'

# A real generation (same shape the bot uses):
curl -s -X POST "https://api.runpod.ai/v2/$TEXT_ENDPOINT_ID/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"task": "generate", "messages": [{"role": "user", "content": "hello"}]}}'
```

First job after deployment downloads ~2.7 GB from Hugging Face (a few
minutes). Later cold starts boot llama-server in ~15–30 s; warm generations
of 1024 tokens typically take a few seconds on an RTX 4090.

## 4. Wire up the bot

Set this on Vercel (the existing `RUNPOD_API_KEY` is reused):

```
RUNPOD_TEXT_ENDPOINT_ID=<text endpoint id>
# TEXT_BACKEND=dummy   # optional: offline stub for testing
```

## Cost sketch

- Network Volume: unchanged — the 2.7 GB GGUF fits in the existing 30 GB
  volume alongside the ~21 GB of image weights.
- GPU time only when jobs run (community RTX 4090 ≈ $0.34–0.44/hr secure
  ≈ $0.69/hr). A warm reply ≈ $0.001–0.005; a cold start adds ~$0.005–0.01.
- RunPod serverless has no per-request surcharge beyond GPU time, and no
  per-endpoint fee.

## Troubleshooting

- **`Jinja Exception: System message must be at the beginning`** — the
  Qwen3.5 chat template rejects system messages placed after conversation
  turns. The worker (v0.4+) front-merges any system messages before calling
  llama-server, so callers can pass them in any order.
- **`unknown model architecture: 'qwen35'`** (v0.1/v0.2) — the prebuilt
  `ghcr.io/ggml-org/llama.cpp:server-cuda-b4738` image predates Qwen3.5.
  Fixed in v0.3+, which compiles llama.cpp from a pinned release tag. If a
  future model needs a newer llama.cpp, bump `LLAMA_CPP_VERSION` in the
  Dockerfile and rebuild.
- **`error while loading shared libraries: libllama.so`** (v0.1) — fixed in
  v0.2+. v0.3+ builds a self-contained static `llama-server`, so the issue
  cannot recur.
- **`unknown task` / bot 404-style errors** — the bot is pointed at the image
  endpoint. `RUNPOD_TEXT_ENDPOINT_ID` must be the *text* endpoint's ID.
- **First job takes a few minutes** — one-time weight download to the volume;
  trigger it once with the `test` task after deploying.
- **Empty or truncated replies** — check `LLAMA_JOB_TIMEOUT` vs. the bot's
  `NSFW_JOB_TIMEOUT_SECONDS` (both default 280 s) and the endpoint's own
  Job Timeout setting (keep it ≥ 300 s).
- **Weaker answers than expected (4B ceiling)** — set `MODEL_GGUF` to the
  Q6_K or Q8_0 quant from the same repo; both still fit the 30 GB volume.

## License note

The model is an abliterated (uncensored) release; running it is your
responsibility. Keep the bot's whitelist (`WHITELIST_IDS`) closed — /nsfw
output is unfiltered by design.
