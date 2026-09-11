#!/usr/bin/env bash
# One-time RunPod network-volume population for EchoMimicV3-Flash weights
# (~25GB total, confirmed via real downloads 2026-09-08/09 -- see
# echomimicv3_spike_test_result.md in project memory). Run ON a temporary
# pod with the target network volume attached and mounted at /workspace.
#
# Uses `hf download` (not raw wget) because all three sources are
# official first-party HuggingFace orgs (alibaba-pai, BadToBest,
# TencentGameMate publish these themselves -- not third-party mirrors of
# an otherwise-unavailable file, unlike MuseTalk's face-parsing checkpoint
# case) with many files each (18 in Wan2.1-Fun alone) -- hand-rolling
# per-file wget URLs the way populate_musetalk_volume.sh (now deleted)
# did would be fragile and miss files on any upstream reorganization.
# `hf download` already verifies each blob's hash against the repo's own
# recorded LFS hash during transfer.
#
# Still explicitly SHA256-verifies the *pickle-format* (.pth/.pt/.bin)
# files after download -- these can execute arbitrary code via
# torch.load(), matching this project's established pickle-checkpoint
# policy (see pth_downloads_need_hash_pinning.md in project memory) even
# though the source here is first-party, not an unofficial mirror.
# .safetensors files are a safe format (no code execution on load) and
# are not separately verified beyond hf download's own transfer check.
set -euo pipefail

pip install -q -U huggingface_hub

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

BASE=/workspace/models/echomimicv3/flash
mkdir -p "$BASE/transformer"

echo "Downloading Wan2.1-Fun-V1.1-1.3B-InP (base model, ~19.8GB)..."
hf download alibaba-pai/Wan2.1-Fun-V1.1-1.3B-InP \
  --local-dir "$BASE/Wan2.1-Fun-V1.1-1.3B-InP"

echo "Downloading chinese-wav2vec2-base (audio encoder, ~1.5GB)..."
hf download TencentGameMate/chinese-wav2vec2-base \
  --local-dir "$BASE/chinese-wav2vec2-base"

echo "Downloading EchoMimicV3-Flash transformer weights (~3.7GB)..."
hf download BadToBest/EchoMimicV3 echomimicv3-flash-pro/diffusion_pytorch_model.safetensors \
  --local-dir /tmp/echomimic_dl
mv /tmp/echomimic_dl/echomimicv3-flash-pro/diffusion_pytorch_model.safetensors \
  "$BASE/transformer/diffusion_pytorch_model.safetensors"

echo "Verifying pickle-format checkpoints (arbitrary-code-execution surface via torch.load)..."
verify_sha256 "$BASE/Wan2.1-Fun-V1.1-1.3B-InP/Wan2.1_VAE.pth" \
  38071ab59bd94681c686fa51d75a1968f64e470262043be31f7a094e442fd981
verify_sha256 "$BASE/Wan2.1-Fun-V1.1-1.3B-InP/models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth" \
  628c9998b613391f193eb67ff68da9667d75f492911e4eb3decf23460a158c38
verify_sha256 "$BASE/Wan2.1-Fun-V1.1-1.3B-InP/models_t5_umt5-xxl-enc-bf16.pth" \
  7cace0da2b446bbbbc57d031ab6cf163a3d59b366da94e5afe36745b746fd81d
verify_sha256 "$BASE/chinese-wav2vec2-base/chinese-wav2vec2-base-fairseq-ckpt.pt" \
  a75e04e426977dd399415b7f586b18978bc6836a3e8514ae1bb29e468fb17184
verify_sha256 "$BASE/chinese-wav2vec2-base/pytorch_model.bin" \
  be2da40c9e7ae26bfc904a3ed79ebb9e8f060bec6dba85d6a6ae86114bc38901

echo "Done, all pickle checkpoints SHA256-verified. Directory sizes:"
du -sh "$BASE"/*
echo "Total:"
du -sh "$BASE"
