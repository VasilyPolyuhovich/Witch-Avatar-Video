#!/usr/bin/env bash
# Run ON the pod, from /opt/SadTalker (the image's baked-in repo checkout +
# baked-in checkpoints/). Wraps inference.py with this project's validated
# flag choices -- see docs/2026-08-13-witch-avatar-video-design.md.
#
# --preprocess full (default): keeps the whole source frame instead of
# cropping to just the face, so wider reference shots (hands/body visible)
# don't get cut down -- only the face region actually animates either way,
# hands/body stay static in the source pose. This is the design's accepted
# limitation, not something this flag can fix; see "Alternatives considered"
# in the design doc for why a full-body-animation model was ruled out.
#
# --still is OFF by default: the design wants "adequate" head motion where
# the source photo supports it (see Requirements: "natural head motion"),
# not a frozen head. Pass --still for a close portrait where head motion
# looks wrong.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_sadtalker.sh --image PATH --audio PATH --output-dir PATH [options]

Required:
  --image PATH          Reference photo (source_image).
  --audio PATH           Driving audio, any format ffmpeg reads (driven_audio).
  --output-dir PATH      Directory the final .mp4 is written into.

Options:
  --preprocess MODE      crop|resize|full|extcrop|extfull (default: full).
  --still                Disable head pose motion (upstream --still).
  --pose-style N          Upstream --pose_style, 0-45 (default: 0).
  --expression-scale N    Upstream --expression_scale (default: 1.0).
EOF
  exit 1
}

IMAGE=""
AUDIO=""
OUTPUT_DIR=""
PREPROCESS="full"
STILL_ARGS=()
POSE_STYLE="0"
EXPRESSION_SCALE="1.0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --audio) AUDIO="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --preprocess) PREPROCESS="$2"; shift 2 ;;
    --still) STILL_ARGS=(--still); shift ;;
    --pose-style) POSE_STYLE="$2"; shift 2 ;;
    --expression-scale) EXPRESSION_SCALE="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
done

[[ -n "$IMAGE" && -n "$AUDIO" && -n "$OUTPUT_DIR" ]] || usage
[[ -f "$IMAGE" ]] || { echo "No such image: $IMAGE" >&2; exit 1; }
[[ -f "$AUDIO" ]] || { echo "No such audio: $AUDIO" >&2; exit 1; }

mkdir -p "$OUTPUT_DIR"
LOG_FILE="$OUTPUT_DIR/inference.log"

cd /opt/SadTalker
python3 inference.py \
  --driven_audio "$AUDIO" \
  --source_image "$IMAGE" \
  --result_dir "$OUTPUT_DIR" \
  --checkpoint_dir ./checkpoints \
  --preprocess "$PREPROCESS" \
  --size 256 \
  --pose_style "$POSE_STYLE" \
  --expression_scale "$EXPRESSION_SCALE" \
  "${STILL_ARGS[@]}" \
  2>&1 | tee "$LOG_FILE"

# inference.py itself prints this exact line right before exiting (see
# main() in that file, fetched from GitHub during planning) -- grep it from
# our own captured log rather than re-deriving the
# {result_dir}/{timestamp}.mp4 naming logic a second time here, so a future
# SadTalker repin can't silently desync the two.
FINAL_FILE=$(grep -oE '^The generated video is named: .+\.mp4' "$LOG_FILE" | tail -1 | sed 's/^The generated video is named: //')
if [[ -z "$FINAL_FILE" || ! -f "$FINAL_FILE" ]]; then
  echo "ERROR: inference.py did not report (or didn't produce) a final output file" >&2
  exit 1
fi
echo "Final output will be: $FINAL_FILE"
