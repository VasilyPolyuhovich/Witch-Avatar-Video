#!/usr/bin/env bash
# Run ON the pod, from /opt/echomimic_v3 (the image's baked-in repo
# checkout). Wraps infer_flash.py with this project's validated flag
# choices -- every flag value here was confirmed working across two real
# end-to-end pod deploys, 2026-09-08 and 2026-09-09 (see
# echomimicv3_spike_test_result.md in project memory).
#
# infer_flash.py hardcodes its weight-directory args as plain relative
# paths (--model_name, --transformer_path, --wav2vec_model_dir all
# resolve relative to CWD) -- this script symlinks the network volume's
# populated weights (see scripts/populate_echomimicv3_volume.sh) into
# ./flash/ so CWD=/opt/echomimic_v3 sees them transparently, matching
# run_musetalk.sh's (now deleted) symlink-into-place pattern.
#
# KNOWN OPEN RISK: EchoMimicV3-Flash's own README caps a single
# generation at 138 frames (5.52s @ 25fps) without "Long Video CFG" --
# neither of this project's two real test renders exceeded 3.24s, so
# whether a bigger --video_length just works past 138 frames is UNTESTED.
# This script computes video_length from the actual audio duration and
# caps it at 138 with a loud warning rather than silently either
# truncating audio or blindly trusting an unverified code path -- see
# Task 11 of docs/superpowers/plans/2026-09-09-echomimicv3-migration-implementation.md.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_echomimicv3.sh --image PATH --audio PATH --output-dir PATH

Required:
  --image PATH          Reference photo (single static image).
  --audio PATH           Driving audio, any format ffmpeg/librosa reads.
  --output-dir PATH      Directory the final .mp4 is written into.
EOF
  exit 1
}

IMAGE=""
AUDIO=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --audio) AUDIO="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
done

[[ -n "$IMAGE" && -n "$AUDIO" && -n "$OUTPUT_DIR" ]] || usage
[[ -f "$IMAGE" ]] || { echo "No such image: $IMAGE" >&2; exit 1; }
[[ -f "$AUDIO" ]] || { echo "No such audio: $AUDIO" >&2; exit 1; }

WEIGHTS=/workspace/models/echomimicv3/flash
if [[ ! -d "$WEIGHTS" ]]; then
  echo "ERROR: $WEIGHTS not found -- is the network volume attached and populated? (see scripts/populate_echomimicv3_volume.sh)" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
cd /opt/echomimic_v3
ln -sfn "$WEIGHTS" /opt/echomimic_v3/flash

FPS=25
MAX_FRAMES=138
AUDIO_DURATION=$(python3 -c "import sys, librosa; print(librosa.get_duration(path=sys.argv[1]))" "$AUDIO")
VIDEO_LENGTH=$(python3 -c "print(max(1, round($AUDIO_DURATION * $FPS)))")
if (( VIDEO_LENGTH > MAX_FRAMES )); then
  echo "WARNING: audio is ${AUDIO_DURATION}s (${VIDEO_LENGTH} frames @ ${FPS}fps)," >&2
  echo "  above EchoMimicV3-Flash's documented 138-frame single-segment cap." >&2
  echo "  Capping at $MAX_FRAMES frames ($(python3 -c "print($MAX_FRAMES/$FPS)")s) --" >&2
  echo "  audio past that point will be cut off. See this project's Task 11" >&2
  echo "  finding for whether Long Video CFG is wired in yet." >&2
  VIDEO_LENGTH=$MAX_FRAMES
fi

LOG_FILE="$OUTPUT_DIR/inference.log"
IMAGE_ABS=$(realpath "$IMAGE")
AUDIO_ABS=$(realpath "$AUDIO")

python3 infer_flash.py \
    --image_path "$IMAGE_ABS" \
    --audio_path "$AUDIO_ABS" \
    --prompt "A person is speaking." \
    --num_inference_steps 8 \
    --config_path "config/config.yaml" \
    --model_name "flash/Wan2.1-Fun-V1.1-1.3B-InP" \
    --ckpt_idx 50000 \
    --transformer_path "flash/transformer/diffusion_pytorch_model.safetensors" \
    --save_path "$OUTPUT_DIR" \
    --wav2vec_model_dir "flash/chinese-wav2vec2-base" \
    --sampler_name "Flow_Unipc" \
    --video_length "$VIDEO_LENGTH" \
    --guidance_scale 6.0 \
    --audio_guidance_scale 3.0 \
    --audio_scale 1.0 \
    --neg_scale 1.0 \
    --neg_steps 0 \
    --seed 43 \
    --enable_teacache \
    --teacache_threshold 0.1 \
    --num_skip_start_steps 5 \
    --riflex_k 6 \
    --ulysses_degree 1 \
    --ring_degree 1 \
    --weight_dtype "bfloat16" \
    --sample_size 768 768 \
    --fps "$FPS" \
    --add_prompt "" \
    --negative_prompt "" \
    --shift 5.0 \
    2>&1 | tee "$LOG_FILE"

# infer_flash.py prints this exact line right before returning on success
# (confirmed via real render logs, 2026-09-08/09) -- grep it from our own
# captured log rather than re-deriving the {save_path}/{image_name}_output.mp4
# naming logic a second time here.
FINAL_FILE=$(grep -oE '^Saved output to: .+\.mp4' "$LOG_FILE" | tail -1 | sed 's/^Saved output to: //')
if [[ -z "$FINAL_FILE" || ! -f "$FINAL_FILE" ]]; then
  echo "ERROR: infer_flash.py did not report (or didn't produce) a final output file" >&2
  exit 1
fi
echo "Final output will be: $FINAL_FILE"
