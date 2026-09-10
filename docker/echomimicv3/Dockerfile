# EchoMimicV3-Flash (antgroup/echomimic_v3) -- replaces docker/musetalk per
# the user's approval after direct video review, 2026-09-09 (see
# echomimicv3_chosen_as_model.md in project memory). Batch-job container,
# same shape as the now-deleted docker/musetalk: no HTTP server, connect
# over SSH. scripts/run_echomimicv3.sh is deliberately NOT baked in here --
# generate_witch_video.py uploads it fresh per job, same reasoning as
# docker/musetalk before it.
#
# Weights (~25GB) live on a RunPod network volume (see
# scripts/populate_echomimicv3_volume.sh), NOT baked into this image.
#
# Pinned: antgroup/echomimic_v3 @ 7e89489ca51c0d008fc1963ec6c03fc5bd0b9397
# (2026-03-18, tip of main as of planning -- confirmed via the GitHub API).
#
# Version pins below are NOT what echomimic_v3's own requirements.txt
# resolves to today -- that file pins almost everything with loose `>=`
# constraints, and by 2026-09 that pulled torch 2.14 (cu130, incompatible
# with a driver capped at CUDA 12.8), transformers 5.16.1 (broke this
# repo's own vendored Wav2Vec2 encoder -- returned hidden_states=None),
# and diffusers 0.40.0 (its flash_attn_3 custom-op needs torch>=2.5). Each
# pin below was confirmed working via two real end-to-end pod deploys,
# 2026-09-08 and 2026-09-09 -- see echomimicv3_spike_test_result.md in
# project memory for the full debugging trail. Do not "helpfully" loosen
# these without re-running that verification.
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Ubuntu 22.04's default python3 is 3.10.x, within echomimic_v3's own
# supported range (3.10/3.11) -- no deadsnakes/PPA needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev build-essential git ffmpeg \
        openssh-server curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

WORKDIR /opt
RUN git clone https://github.com/antgroup/echomimic_v3.git /opt/echomimic_v3 \
    && cd /opt/echomimic_v3 \
    && git checkout 7e89489ca51c0d008fc1963ec6c03fc5bd0b9397

WORKDIR /opt/echomimic_v3
RUN python3 -m pip install --upgrade pip \
    && pip install -r requirements.txt pyloudnorm

# Confirmed working combo (2026-09-08/09 spike tests): satisfies both
# diffusers>=0.30.1 (this repo's floor) needing torch>=2.5 for its
# flash_attn_3 custom-op registration, AND a cu124 wheel compatible with
# hosts whose driver caps at CUDA 12.8 (a plain `pip install torch` today
# resolves to cu130, which silently makes torch.cuda.is_available() False
# on such hosts -- no error, just no GPU).
RUN pip install --force-reinstall \
        torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 \
        --index-url https://download.pytorch.org/whl/cu124

# transformers 5.16.1 (2026-09's latest) broke this repo's own vendored
# src/wav2vec2.py encoder -- hidden_states came back None. diffusers
# 0.40.0 needs torch>=2.5 (see above). Pinned to the last releases before
# this repo's own last update (2026-01-22), both confirmed working.
RUN pip install transformers==4.57.6 diffusers==0.36.0

# tensorflow==2.15.0 (pinned in requirements.txt for the *preview* path's
# retinaface face-mask code, not used by infer_flash.py -- the Flash
# variant's whole selling point is "No Face Mask required") breaks under
# the numpy>=2.0 that other now-latest packages pull in
# (ml_dtypes/_ARRAY_API not found). transformers 4.57.6 still imports
# tensorflow unconditionally in image_transforms.py despite it being
# optional elsewhere -- uninstalling it entirely makes
# importlib.util.find_spec report "not installed" and that import
# skipped cleanly, rather than tensorflow being present-but-broken (which
# crashes at import time).
RUN pip uninstall -y tensorflow tensorboard retina-face

COPY entrypoint.sh /opt/
RUN chmod +x /opt/entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/opt/entrypoint.sh"]
