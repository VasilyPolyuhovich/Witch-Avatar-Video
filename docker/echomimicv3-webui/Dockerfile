# Real WebUI backend for EchoMimicV3-Flash -- implements the same
# /health,/heartbeat,/generate,/status,/result contract as
# docker/webui-stub (which this replaces), but runs real inference. Same
# pinned commit and version fixes as docker/echomimicv3/Dockerfile --
# see that file's comments for the full rationale; kept in sync
# deliberately (both images clone the same commit and apply the same
# pins), not shared via a base image, matching this project's existing
# pattern of independent per-purpose Dockerfiles.
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev build-essential git ffmpeg \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone https://github.com/antgroup/echomimic_v3.git /opt/echomimic_v3 \
    && cd /opt/echomimic_v3 \
    && git checkout 7e89489ca51c0d008fc1963ec6c03fc5bd0b9397

WORKDIR /opt/echomimic_v3
RUN python3 -m pip install --upgrade pip \
    && pip install -r requirements.txt pyloudnorm

RUN pip install --force-reinstall \
        torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 \
        --index-url https://download.pytorch.org/whl/cu124

RUN pip install transformers==4.57.6 diffusers==0.36.0
RUN pip uninstall -y tensorflow tensorboard retina-face

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app.py /app/app.py
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
