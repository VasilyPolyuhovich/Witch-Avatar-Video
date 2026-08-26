# SadTalker portrait/head animation, weights baked in at build time (under
# 1GB total -- small enough to skip AI-Avatar-Video's network-volume
# approach entirely, see docs/2026-08-13-witch-avatar-video-design.md).
# Batch-job container, same shape as AI-Avatar-Video's docker/longcat-avatar:
# no HTTP server, connect over SSH. scripts/run_sadtalker.sh is deliberately
# NOT baked in here -- generate_witch_video.py uploads it fresh per job, so
# its render-invocation logic can be tuned without rebuilding this image
# (mirrors AI-Avatar-Video's run_longcat_avatar.sh, same reasoning).
#
# Pinned: SadTalker @ cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8 (2023-10-10,
# tip of main -- the repo has had no commits since).
FROM nvidia/cuda:11.3.1-devel-ubuntu20.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Ubuntu 20.04's default python3 is 3.8.x -- exactly what SadTalker's own
# install docs and cog.yaml target, no deadsnakes/PPA needed.
#
# pkg-config + libav*-dev beyond cog.yaml's own system_packages (which lists
# only ffmpeg/libgl1-mesa-glx/libglib2.0-0): Replicate's Cog build resolves
# a prebuilt av==10.0.0 wheel, but building this image (amd64, possibly
# under QEMU emulation on an Apple Silicon dev machine) hit a pip fallback
# to source, which needs these to compile PyAV -- added defensively so the
# build doesn't depend on which path pip takes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev build-essential git ffmpeg \
        libgl1-mesa-glx libglib2.0-0 libsndfile1 \
        pkg-config libavformat-dev libavcodec-dev libavdevice-dev \
        libavutil-dev libswscale-dev libswresample-dev libavfilter-dev \
        openssh-server curl ca-certificates wget \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

WORKDIR /opt
RUN git clone https://github.com/OpenTalker/SadTalker.git /opt/SadTalker \
    && cd /opt/SadTalker \
    && git checkout cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8

WORKDIR /opt/SadTalker
RUN python3 -m pip install --upgrade pip \
    && pip install torch==1.12.1+cu113 torchvision==0.13.1+cu113 torchaudio==0.12.1 \
       --extra-index-url https://download.pytorch.org/whl/cu113

# Pins match cog.yaml (OpenTalker's own Replicate production config) rather
# than requirements.txt where the two differ. dlib-bin (prebuilt wheel)
# instead of dlib avoids a slow from-source compile; trimesh is a
# face_alignment/basicsr transitive dependency cog.yaml pins explicitly.
# av==10.0.0 is installed separately below -- see that step for why.
RUN pip install \
        numpy==1.23.4 \
        face_alignment==1.3.5 \
        imageio==2.19.3 \
        imageio-ffmpeg==0.4.7 \
        librosa==0.9.2 \
        numba \
        resampy==0.3.1 \
        pydub==0.25.1 \
        scipy==1.10.1 \
        kornia==0.6.8 \
        tqdm==4.65.0 \
        yacs==0.1.8 \
        pyyaml \
        joblib==1.1.0 \
        scikit-image==0.19.3 \
        basicsr==1.4.2 \
        facexlib==0.3.0 \
        safetensors \
        gfpgan==1.3.8 \
        dlib-bin==19.24.1 \
        trimesh==3.9.20

# av==10.0.0 ships only a source distribution on PyPI (no wheels at all,
# confirmed via PyPI's JSON API) and its pyproject.toml leaves its Cython
# build dependency unpinned ("cython", no upper bound). Since Cython 3.0
# (2023, after av 10.0.0's Feb-2023 release), pip's isolated build env
# resolves Cython 3.x by default, which fails compiling av's logging.pyx
# with "Cannot assign type ... Exception values are incompatible" --
# Cython 3 tightened `nogil` exception-spec checking in a way this file's
# old-style callback declarations don't satisfy. Fix: pin Cython to the
# last 0.29.x release and force pip to use it via --no-build-isolation
# (otherwise pip would still resolve a fresh, unpinned Cython inside an
# isolated build env regardless of what's installed in the image).
RUN pip install "Cython==0.29.37" \
    && pip install --no-build-isolation av==10.0.0

# face_alignment's own face-detector weights: normally auto-downloaded from
# adrianbulat.com on first use inside inference.py -- baked in here instead
# so a render never depends on that host being up, and doesn't pay the
# download time inside the billed render window. Same two files/URLs
# cog.yaml itself bakes in for the same reason.
RUN mkdir -p /root/.cache/torch/hub/checkpoints/ \
    && wget -nv -O /root/.cache/torch/hub/checkpoints/s3fd-619a316812.pth \
       https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth \
    && wget -nv -O /root/.cache/torch/hub/checkpoints/2DFAN4-cd938726ad.zip \
       https://www.adrianbulat.com/downloads/python-fan/2DFAN4-cd938726ad.zip

# Model checkpoints, baked in (confirmed via the GitHub releases API during
# planning: mapping x2 ~148MB each + one safetensors variant ~691MB, under
# 1GB total). Only the 256px safetensors variant -- matches
# run_sadtalker.sh's fixed --size 256 -- and both mapping nets (crop vs
# full preprocessing pick different ones at runtime, see src/utils/init_path.py).
# GFPGAN enhancer WEIGHTS (as opposed to the gfpgan pip package installed
# above, which SadTalker imports at module load time regardless) are
# deliberately excluded: the design's quality bar is explicitly "not
# hyper-realism", so the extra ~700MB+ and render time buys nothing this
# project needs. --enhancer gfpgan will fail without them; that's fine, the
# project doesn't pass that flag (see run_sadtalker.sh).
RUN mkdir -p checkpoints \
    && wget -nv -O checkpoints/mapping_00109-model.pth.tar \
       https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/mapping_00109-model.pth.tar \
    && wget -nv -O checkpoints/mapping_00229-model.pth.tar \
       https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/mapping_00229-model.pth.tar \
    && wget -nv -O checkpoints/SadTalker_V0.0.2_256.safetensors \
       https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/SadTalker_V0.0.2_256.safetensors

COPY entrypoint.sh /opt/
RUN chmod +x /opt/entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/opt/entrypoint.sh"]
