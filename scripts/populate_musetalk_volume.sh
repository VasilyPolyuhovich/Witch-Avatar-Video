#!/usr/bin/env bash
# One-time RunPod network-volume population for MuseTalk weights (~4.1GB
# total, confirmed via HTTP HEAD requests during planning -- see
# docs/superpowers/specs/2026-08-28-musetalk-migration-design.md).
# Run ON a temporary pod with the target network volume attached and
# mounted at /workspace (see docs/superpowers/plans/2026-08-28-musetalk-migration-implementation.md
# Task 5 for how to deploy that pod). NOT part of the per-render hot path --
# the resulting /workspace/models/ persists on the volume and is reused by
# every future render pod via scripts/run_musetalk.sh's symlink step.
#
# Every download is SHA256-verified against a hash pinned below (added
# after a security review flagged this script pulling a .pth file --
# a pickle-based format capable of arbitrary code execution on load via
# torch.load() -- from a third-party Hugging Face mirror account
# (vivym/face-parsing-bisenet), not an official source. The official
# upstream (zllrunning/face-parsing.PyTorch) only hosts this checkpoint
# on Google Drive, which isn't wget-friendly for a reproducible script.
# Mitigation applied: the hash below was cross-checked against two other
# independent mirror accounts (afrizalha/musetalk-models,
# ManyOtherFunctions/face-parse-bisent) and all three serve byte-identical
# content, then pinned here so any future change to any of these sources
# -- accidental or malicious -- makes this script fail loudly instead of
# silently accepting different content. Applied the same verification to
# every other download too, not just this one, as standard practice.
#
# Residual, knowingly-accepted risk: MuseTalk's own vendored code
# (musetalk/utils/face_parsing/__init__.py, part of the pinned repo
# checkout baked into docker/musetalk/Dockerfile, not this script) loads
# these .pth files via plain torch.load() without weights_only=True,
# which -- for a file whose content this script no longer merely trusts
# but hash-verifies -- is an acceptable residual gap. Fixing it would mean
# patching vendored third-party source, out of scope for this download
# script; revisit only if MuseTalk's own upstream fixes it or this
# project starts maintaining a fork.
set -euo pipefail

verify_sha256() {
  local file="$1" expected="$2" actual
  actual=$(sha256sum "$file" | awk '{print $1}')
  if [[ "$actual" != "$expected" ]]; then
    echo "ERROR: SHA256 mismatch for $file" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
}

fetch() {
  local dest="$1" url="$2" expected_sha256="$3"
  wget -nv -O "$dest" "$url"
  verify_sha256 "$dest" "$expected_sha256"
}

mkdir -p /workspace/models/musetalkV15
mkdir -p /workspace/models/sd-vae
mkdir -p /workspace/models/whisper
mkdir -p /workspace/models/dwpose
mkdir -p /workspace/models/face-parse-bisent

echo "Downloading musetalkV15 (unet + config, ~3.17GB)..."
fetch /workspace/models/musetalkV15/unet.pth \
    https://huggingface.co/TMElyralab/MuseTalk/resolve/main/musetalkV15/unet.pth \
    7ebf6c98c181e20838e4c0054e96e944ac60d5d692cc01db42839fe11b787007
fetch /workspace/models/musetalkV15/musetalk.json \
    https://huggingface.co/TMElyralab/MuseTalk/resolve/main/musetalkV15/musetalk.json \
    5b6923aee04d71692e0e9846c471e0a4ea07a4f686d39545e472bd4ba17e1b47

echo "Downloading sd-vae (~0.31GB)..."
fetch /workspace/models/sd-vae/config.json \
    https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/config.json \
    92d3dfb746fca211a2c9e019e285f8597412211728dce3c5bcf4eda0f2d62e7e
fetch /workspace/models/sd-vae/diffusion_pytorch_model.safetensors \
    https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/diffusion_pytorch_model.safetensors \
    a1d993488569e928462932c8c38a0760b874d166399b14414135bd9c42df5815

echo "Downloading whisper (~0.14GB)..."
fetch /workspace/models/whisper/config.json \
    https://huggingface.co/openai/whisper-tiny/resolve/main/config.json \
    ffdccec4f3211f4c63310f2b7098f309fe70f3952cedc5e4d11e43f5b2379b98
fetch /workspace/models/whisper/preprocessor_config.json \
    https://huggingface.co/openai/whisper-tiny/resolve/main/preprocessor_config.json \
    9b5cd03a36fbb8a627c64d98a5b5b126ead95a77720723944487311f0110b666
fetch /workspace/models/whisper/model.safetensors \
    https://huggingface.co/openai/whisper-tiny/resolve/main/model.safetensors \
    7ebd0e69e78190ffe1438491fa05cc1f5c1aa3a4c4db3bc1723adbb551ea2395

echo "Downloading dwpose (~0.38GB)..."
fetch /workspace/models/dwpose/dw-ll_ucoco_384.pth \
    https://huggingface.co/yzd-v/DWPose/resolve/main/dw-ll_ucoco_384.pth \
    0d9408b13cd863c4e95a149dd31232f88f2a12aa6cf8964ed74d7d97748c7a07

echo "Downloading face-parse-bisent (~0.10GB; 79999_iter.pth via a Hugging"
echo "Face mirror -- upstream only hosts it on Google Drive; see the header"
echo "comment above for the verification this hash pin is based on)..."
fetch /workspace/models/face-parse-bisent/79999_iter.pth \
    https://huggingface.co/vivym/face-parsing-bisenet/resolve/main/79999_iter.pth \
    468e13ca13a9b43cc0881a9f99083a430e9c0a38abd935431d1c28ee94b26567
fetch /workspace/models/face-parse-bisent/resnet18-5c106cde.pth \
    https://download.pytorch.org/models/resnet18-5c106cde.pth \
    5c106cde386e87d4033832f2996f5493238eda96ccf559d1d62760c4de0613f8

echo "Done, all downloads SHA256-verified. Directory sizes:"
du -sh /workspace/models/*
echo "Total:"
du -sh /workspace/models
