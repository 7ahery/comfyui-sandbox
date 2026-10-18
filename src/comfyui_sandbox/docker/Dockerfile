FROM python:3.12-slim-trixie

ARG PUID=1000
ARG PGID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git curl wget unzip \
    build-essential cmake pkg-config \
    ffmpeg \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    libsndfile1 libgomp1 \
    fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g ${PGID} comfy && useradd -m -u ${PUID} -g ${PGID} -s /bin/bash comfy

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

ENV HOME=/workspace/home \
    VIRTUAL_ENV=/workspace/venv \
    PATH=/workspace/venv/bin:$PATH \
    # Download caches (pip, and uv which comfy-cli installs with) on the bind-mounted
    # /workspace, named explicitly so they stay there whatever HOME or XDG_CACHE_HOME
    # say. A first start that is cut off (timeout, container stopped, WSL shut down)
    # then finds the finished wheels (PyTorch is several GB) on the next start.
    PIP_CACHE_DIR=/workspace/home/.cache/pip \
    UV_CACHE_DIR=/workspace/home/.cache/uv \
    PYTHONUNBUFFERED=1 \
    DO_NOT_TRACK=1 \
    COMFY_NO_TELEMETRY=1

USER comfy
WORKDIR /workspace
EXPOSE 8188
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
