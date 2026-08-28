#!/usr/bin/env bash
# One-time RunPod network-volume population for MuseTalk weights (~4.1GB
# total, confirmed via HTTP HEAD requests during planning -- see
# docs/superpowers/specs/2026-08-28-musetalk-migration-design.md).
# Run ON a temporary pod with the target network volume attached and
# mounted at /workspace (see docs/superpowers/plans/2026-08-28-musetalk-migration-implementation.md
# Task 5 for how to deploy that pod). NOT part of the per-render hot path --
# the resulting /workspace/models/ persists on the volume and is reused by
# every future render pod via scripts/run_musetalk.sh's symlink step.
set -euo pipefail

mkdir -p /workspace/models/musetalkV15
mkdir -p /workspace/models/sd-vae
mkdir -p /workspace/models/whisper
mkdir -p /workspace/models/dwpose
mkdir -p /workspace/models/face-parse-bisent

echo "Downloading musetalkV15 (unet + config, ~3.17GB)..."
wget -nv -O /workspace/models/musetalkV15/unet.pth \
    https://huggingface.co/TMElyralab/MuseTalk/resolve/main/musetalkV15/unet.pth
wget -nv -O /workspace/models/musetalkV15/musetalk.json \
    https://huggingface.co/TMElyralab/MuseTalk/resolve/main/musetalkV15/musetalk.json

echo "Downloading sd-vae (~0.31GB)..."
wget -nv -O /workspace/models/sd-vae/config.json \
    https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/config.json
wget -nv -O /workspace/models/sd-vae/diffusion_pytorch_model.safetensors \
    https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/diffusion_pytorch_model.safetensors

echo "Downloading whisper (~0.14GB)..."
wget -nv -O /workspace/models/whisper/config.json \
    https://huggingface.co/openai/whisper-tiny/resolve/main/config.json
wget -nv -O /workspace/models/whisper/preprocessor_config.json \
    https://huggingface.co/openai/whisper-tiny/resolve/main/preprocessor_config.json
wget -nv -O /workspace/models/whisper/model.safetensors \
    https://huggingface.co/openai/whisper-tiny/resolve/main/model.safetensors

echo "Downloading dwpose (~0.38GB)..."
wget -nv -O /workspace/models/dwpose/dw-ll_ucoco_384.pth \
    https://huggingface.co/yzd-v/DWPose/resolve/main/dw-ll_ucoco_384.pth

echo "Downloading face-parse-bisent (~0.10GB; 79999_iter.pth via a Hugging"
echo "Face mirror -- upstream only hosts it on Google Drive)..."
wget -nv -O /workspace/models/face-parse-bisent/79999_iter.pth \
    https://huggingface.co/vivym/face-parsing-bisenet/resolve/main/79999_iter.pth
wget -nv -O /workspace/models/face-parse-bisent/resnet18-5c106cde.pth \
    https://download.pytorch.org/models/resnet18-5c106cde.pth

echo "Done. Directory sizes:"
du -sh /workspace/models/*
echo "Total:"
du -sh /workspace/models
