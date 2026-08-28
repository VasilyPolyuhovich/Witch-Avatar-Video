# MuseTalk audio-driven lip-sync (static head, latent-space inpainting) --
# replaces docker/sadtalker per
# docs/superpowers/specs/2026-08-28-musetalk-migration-design.md (SadTalker
# produced no real lip-sync and chaotic motion in two real test renders).
# Batch-job container, same shape as docker/sadtalker: no HTTP server,
# connect over SSH. scripts/run_musetalk.sh is deliberately NOT baked in
# here -- generate_witch_video.py uploads it fresh per job, same reasoning
# as docker/sadtalker.
#
# UNLIKE docker/sadtalker, model weights are NOT baked into this image.
# MuseTalk's inference weights total ~4.1GB (vs SadTalker's <1GB,
# confirmed via HTTP HEAD requests during planning) -- too big to justify
# baking in, so they live on a RunPod network volume instead (see
# scripts/populate_musetalk_volume.sh), mounted at /workspace and
# symlinked into MuseTalk's expected ./models/ location by
# scripts/run_musetalk.sh (MuseTalk hardcodes several weight paths as
# "./models/<name>", relative to the process's CWD, not overridable via
# any CLI flag -- confirmed by reading musetalk/utils/utils.py and
# musetalk/utils/face_parsing/__init__.py directly).
#
# Pinned: MuseTalk @ 0a89dec45a0192b824e3cf4daf96c239440c5ed8 (2025-09-26,
# tip of main as of planning -- confirmed via the GitHub API).
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Ubuntu 22.04's default python3 is 3.10.x -- exactly what MuseTalk's own
# install docs target, no deadsnakes/PPA needed (same reasoning as
# docker/sadtalker's Ubuntu 20.04/Python 3.8 pairing). xz-utils is needed
# to extract the static ffmpeg build below (a plain .tar.xz).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev build-essential git \
        libgl1-mesa-glx libglib2.0-0 libsndfile1 \
        openssh-server curl ca-certificates wget xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

WORKDIR /opt
RUN git clone https://github.com/TMElyralab/MuseTalk.git /opt/MuseTalk \
    && cd /opt/MuseTalk \
    && git checkout 0a89dec45a0192b824e3cf4daf96c239440c5ed8

WORKDIR /opt/MuseTalk
RUN python3 -m pip install --upgrade pip \
    && pip install torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 \
       --index-url https://download.pytorch.org/whl/cu118

# Verbatim from MuseTalk's own requirements.txt (fetched during planning),
# not hand-picked -- matches this project's own "pins match the upstream
# maintainer's own config" convention already used for docker/sadtalker.
RUN pip install \
        diffusers==0.30.2 \
        accelerate==0.28.0 \
        numpy==1.23.5 \
        tensorflow==2.12.0 \
        tensorboard==2.12.0 \
        opencv-python==4.9.0.80 \
        soundfile==0.12.1 \
        transformers==4.39.2 \
        huggingface_hub==0.30.2 \
        librosa==0.11.0 \
        einops==0.8.1 \
        gradio==5.24.0 \
        gdown \
        requests \
        "imageio[ffmpeg]" \
        omegaconf \
        ffmpeg-python \
        moviepy

# Deviation from MuseTalk's own README, added after a real build failure
# (not speculative): mmpose==1.1.0 unconditionally depends on `chumpy`
# (confirmed unused by MuseTalk itself -- open-mmlab/mmpose#3218 tracks
# removing this dependency entirely), and chumpy==0.70's setup.py does
# `import pip` / `from pip._internal.req import parse_requirements` at
# module load time (mattloper/chumpy#56) -- a pre-PEP-517 pattern that
# breaks under pip's default isolated build environment, which contains
# only setuptools/wheel, not pip itself. Actual error hit: "ERROR: Failed
# to build 'chumpy' when getting requirements to build wheel" /
# "ModuleNotFoundError: No module named 'pip'". Installing chumpy on its
# own first with --no-build-isolation runs its setup.py against the
# ambient environment (where pip *is* installed) instead of an isolated
# one, sidestepping the bug; `mim install mmpose` below then finds chumpy
# already satisfied and never tries to build it itself.
RUN pip install --no-cache-dir --no-build-isolation "chumpy==0.70"

# OpenMMLab ecosystem, per MuseTalk's own install docs -- known for being
# order- and version-sensitive (mmcv must resolve against the exact
# installed torch/CUDA build), hence sequential `mim install` calls
# exactly as the README specifies, rather than a flat pip install list.
RUN pip install --no-cache-dir -U openmim \
    && mim install mmengine \
    && mim install "mmcv==2.0.1" \
    && mim install "mmdet==3.1.0" \
    && mim install "mmpose==1.1.0"

# Static ffmpeg build -- MuseTalk's inference.py takes an explicit
# --ffmpeg_path rather than relying on system ffmpeg (see
# scripts/run_musetalk.sh). Pinned to the n8.1 versioned release, not the
# rolling "master-latest" tag, for reproducibility (confirmed available
# via the GitHub releases API during planning).
RUN wget -nv -O /tmp/ffmpeg.tar.xz \
       https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-shared-8.1.tar.xz \
    && mkdir -p /opt/ffmpeg \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1 \
    && rm /tmp/ffmpeg.tar.xz

COPY entrypoint.sh /opt/
RUN chmod +x /opt/entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/opt/entrypoint.sh"]
