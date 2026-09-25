# RunPod Serverless worker for HauhauCS Qwen3.5-4B Uncensored (llama.cpp).
#
# llama.cpp is compiled from source: the official GHCR server-cuda images
# stopped publishing in early 2025 and predate the Qwen3.5 ("qwen35")
# architecture, so a current release tag is pinned and built for the
# endpoint's GPU architecture. Bump LLAMA_CPP_VERSION and rebuild to move to
# a newer llama.cpp (e.g. when a future Qwen release needs it).
#
# Build and push (both tags point at the same image; pin endpoints to the
# versioned tag), then point a RunPod Serverless endpoint at the image:
#   docker build -t <registry>/hauhaucs-text-runpod:v0.3 \
#                -t <registry>/hauhaucs-text-runpod:latest ./runpod-text
#   docker push <registry>/hauhaucs-text-runpod:v0.3
#   docker push <registry>/hauhaucs-text-runpod:latest
#
# The GGUF weights are NOT baked into the image. They are downloaded once from
# Hugging Face at first boot into MODEL_CACHE_DIR (a Network Volume in
# production, shared with the image worker) and reused on every later cold
# start.

# --- Build stage: compile llama-server with CUDA ---------------------------
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS build
# llama.cpp release tag with Qwen3.5 support (qwen35 + qwen35moe landed well
# after the last prebuilt server-cuda image, which is why this is a source
# build). Pick the newest release tag from github.com/ggml-org/llama.cpp/releases.
ARG LLAMA_CPP_VERSION=b11173
# 89 = RTX 4090 (the recommended endpoint GPU). Add architectures here (e.g.
# "80;86;89" for A100/3090 support) if you plan to switch GPU classes — each
# one adds compile time.
ARG CUDA_ARCHITECTURES=89
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential cmake libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${LLAMA_CPP_VERSION} https://github.com/ggml-org/llama.cpp /src
WORKDIR /src
# BUILD_SHARED_LIBS=ON avoids one giant static-link step whose memory peak
# OOMs small builder VMs (a 5.7 GB Docker Desktop VM wedged on it); -j 2 caps
# the parallel nvcc memory the same way. On a big builder both can be raised.
# The cache mount keeps compiled objects across builds, so retries after a
# failure resume instead of recompiling ~40 min of CUDA kernels.
# The stubs link dir provides the CUDA driver symbols (cuGetErrorString etc.)
# at link time; the real driver lib is injected at runtime by the NVIDIA
# container runtime.
RUN --mount=type=cache,target=/src/build \
    cmake -B build -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES} \
        "-DCMAKE_EXE_LINKER_FLAGS=-L/usr/local/cuda/lib64/stubs -lcuda" \
    && cmake --build build --target llama-server -j 2 \
    && mkdir -p /out && cp -a build/bin/. /out/

# --- Runtime stage ---------------------------------------------------------
# Same CUDA runtime base the old prebuilt images used, so the NVIDIA container
# runtime keeps injecting the driver and the bundled CUDA user libraries
# (cudart/cublas) match what the binary was linked against.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip libcurl4 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# llama-server plus its shared libs (libllama.so, libggml*.so) all land in
# build/bin together; LD_LIBRARY_PATH makes the loader find them next to the
# binary (the NVIDIA dirs keep the driver lib resolving on RunPod).
COPY --from=build /out /app

COPY requirements.txt /runpod-worker/requirements.txt
RUN pip3 install --no-cache-dir -r /runpod-worker/requirements.txt

WORKDIR /runpod-worker
COPY handler.py /runpod-worker/handler.py

ENV RUNPOD_VOLUME_PATH=/runpod-volume \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    LD_LIBRARY_PATH=/app:/usr/local/nvidia/lib:/usr/local/nvidia/lib64

ENTRYPOINT []
CMD ["python3", "-u", "/runpod-worker/handler.py"]
