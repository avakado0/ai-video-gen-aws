# The GPU worker image. Build context is the repo root (it needs lambdas/common as well):
#   ./scripts/build_push.sh
# NOT BUILT YET. Needs about 25 GB of free disk and a few minutes; the result is about 8 GB.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/lambdas \
    # ECS injects the GPU device; this asks the NVIDIA runtime to mount the driver libraries too.
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    # Weights come from /data/models (copied from S3 at boot). Never reach out to Hugging Face.
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app

# Dependencies first, so editing the code does not reinstall torch.
COPY worker/requirements.txt worker/requirements.txt
RUN pip install -r worker/requirements.txt

COPY lambdas/common lambdas/common
COPY worker worker

CMD ["python", "-m", "worker"]
