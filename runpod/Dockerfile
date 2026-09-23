# RunPod Serverless worker for Qwen Image 2.1 GGUF (ComfyUI backend).
#
# Build and push, then point a RunPod Serverless endpoint at the image:
#   docker build -t <registry>/qwen-image-2.1-runpod:latest ./runpod
#   docker push <registry>/qwen-image-2.1-runpod:latest
#
# Model weights are NOT baked into the image. They are downloaded once from
# Hugging Face at first boot into MODEL_CACHE_DIR (a Network Volume in
# production) and reused on every later cold start.

FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive
# build-essential: ComfyUI's comfy_kitchen/triton dependency JIT-compiles its
# CUDA driver shim with gcc at import time; runtime CUDA images ship no compiler.
# libcuda.so symlink: the driver lib is mounted at runtime by the NVIDIA
# container runtime (only as libcuda.so.1); triton's shim links against -lcuda.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/lib/x86_64-linux-gnu/libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so

# ComfyUI, pinned to a commit with the Qwen-Image-2.1 nodes
# (TextEncodeQwenImage21, QwenImage21Cache).
ARG COMFYUI_COMMIT=95539f56344958339e39b7582a476267d489b0ee
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /ComfyUI \
    && git -C /ComfyUI checkout ${COMFYUI_COMMIT} \
    && pip install --no-cache-dir -r /ComfyUI/requirements.txt

# GGUF loader support. The leejet fork is required: the older city96 version
# fails on Qwen-Image-2.1 GGUFs with "Unknown model architecture!".
# Its requirements (the gguf package) must be installed or the node silently
# fails to register ("Node 'UnetLoaderGGUF' not found").
ARG COMFYUI_GGUF_COMMIT=edd981b10e107d3b8f58e16c498f2d08f631bc47
RUN git clone https://github.com/leejet/ComfyUI-GGUF.git /ComfyUI/custom_nodes/ComfyUI-GGUF \
    && git -C /ComfyUI/custom_nodes/ComfyUI-GGUF checkout ${COMFYUI_GGUF_COMMIT} \
    && pip install --no-cache-dir -r /ComfyUI/custom_nodes/ComfyUI-GGUF/requirements.txt

COPY requirements.txt /runpod-worker/requirements.txt
RUN pip install --no-cache-dir -r /runpod-worker/requirements.txt

WORKDIR /runpod-worker
COPY handler.py /runpod-worker/handler.py
COPY workflows /runpod-worker/workflows

ENV COMFYUI_DIR=/ComfyUI \
    COMFYUI_PORT=8188 \
    RUNPOD_VOLUME_PATH=/runpod-volume \
    HF_HUB_ENABLE_HF_TRANSFER=0

CMD ["python", "-u", "/runpod-worker/handler.py"]
