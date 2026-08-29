#!/usr/bin/env bash
# Run ON the pod, from /opt/MuseTalk (the image's baked-in repo checkout).
# Wraps scripts.inference with this project's validated flag choices --
# see docs/superpowers/specs/2026-08-28-musetalk-migration-design.md.
#
# MuseTalk hardcodes several weight paths as "./models/<name>", relative
# to the process's CWD, with NO CLI override (confirmed by reading
# musetalk/utils/utils.py's load_all_model() and
# musetalk/utils/face_parsing/__init__.py's FaceParsing.model_init()
# directly during planning -- not guessed). The network volume mounts at
# /workspace/models (populated once by scripts/populate_musetalk_volume.sh);
# this script symlinks it into place so CWD=/opt/MuseTalk sees
# ./models/... transparently pointing at the volume, while everything
# else MuseTalk needs from its own repo checkout (dwpose configs, etc.)
# still resolves normally against /opt/MuseTalk.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_musetalk.sh --image PATH --audio PATH --output-dir PATH

Required:
  --image PATH          Reference photo (single static image; MuseTalk
                          also accepts video, but this project only ever
                          passes a still portrait).
  --audio PATH           Driving audio, any format ffmpeg reads.
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

if [[ ! -d /workspace/models ]]; then
  echo "ERROR: /workspace/models not found -- is the network volume attached and populated? (see scripts/populate_musetalk_volume.sh)" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
LOG_FILE="$OUTPUT_DIR/inference.log"

cd /opt/MuseTalk
ln -sfn /workspace/models /opt/MuseTalk/models

# MuseTalk's own inference.py (this pinned commit, vendored in the Docker
# image -- not something this script patches) has two bugs when
# video_path in the config points directly at an image file, the shape
# the original design assumed was safe (see
# docs/superpowers/specs/2026-08-28-musetalk-migration-design.md's "no
# MuseV step needed" note) -- both confirmed by a real end-to-end test
# render against a 717x1280 portrait, not guessed from re-reading the
# source a second time:
#
#   1. Its final img2video ffmpeg call hardcodes "-vf format=yuv420p"
#      with no even-dimension guard, and the output frame size for a
#      single-image input equals the *source* image's own dimensions
#      (musetalk/utils/blending.py's get_image() pastes the generated
#      mouth back into a full copy of the source frame -- "整张图", the
#      whole image, not a face-sized canvas). An odd-width source photo
#      makes libx264 refuse to open the encoder ("width not divisible by
#      2"), leaving an empty/corrupt output file.
#   2. Its per-task cleanup unconditionally runs
#      `shutil.rmtree(save_dir_full)`, but `save_dir_full` is only ever
#      assigned in the `get_file_type(video_path) == "video"` branch --
#      never in the "image" branch. Every image-only run therefore hits
#      `UnboundLocalError: local variable 'save_dir_full' referenced
#      before assignment` during cleanup, regardless of bug #1, and
#      inference.py's own per-task try/except swallows it as "Error
#      occurred during processing" -- so the "Results saved to ..." line
#      this script's FINAL_FILE extraction relies on is, on this pinned
#      commit, unreachable for a plain image input, full stop.
#
# Sidestep both from our side rather than patch the vendored, pinned
# inference.py: re-encode the source image as a one-frame video with
# guaranteed-even dimensions, and feed *that* to MuseTalk as video_path.
# This routes execution through the "video" branch instead (which does
# assign save_dir_full), and the frame MuseTalk extracts from it is
# already even-dimensioned, so the img2video step never hits bug #1
# either. Functionally identical to a still image as far as MuseTalk's
# own frame-cycling logic is concerned (datagen() cycles through
# frame_list_cycle by index modulo its length regardless of how many
# distinct frames it started from).
NORMALIZED_VIDEO=$(mktemp --suffix=.mp4)
/opt/ffmpeg/bin/ffmpeg -y -v error -i "$IMAGE" -frames:v 1 -r 25 \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -pix_fmt yuv420p -c:v libx264 \
  "$NORMALIZED_VIDEO"
IMAGE="$NORMALIZED_VIDEO"

CONFIG_FILE=$(mktemp --suffix=.yaml)
trap 'rm -f "$CONFIG_FILE" "$NORMALIZED_VIDEO"' EXIT
cat > "$CONFIG_FILE" <<EOF
job:
 video_path: "$IMAGE"
 audio_path: "$AUDIO"
EOF

python3 -m scripts.inference \
  --inference_config "$CONFIG_FILE" \
  --result_dir "$OUTPUT_DIR" \
  --unet_model_path /workspace/models/musetalkV15/unet.pth \
  --unet_config /workspace/models/musetalkV15/musetalk.json \
  --whisper_dir /workspace/models/whisper \
  --vae_type sd-vae \
  --version v15 \
  --ffmpeg_path /opt/ffmpeg/bin \
  2>&1 | tee "$LOG_FILE"

# scripts.inference itself prints this exact line right before returning
# on success (see main() in that file, fetched from GitHub during
# planning) -- grep it from our own captured log rather than re-deriving
# the {result_dir}/{version}/{basename}.mp4 naming logic a second time
# here, so a future MuseTalk repin can't silently desync the two (same
# reasoning as run_sadtalker.sh's FINAL_FILE extraction).
FINAL_FILE=$(grep -oE '^Results saved to .+\.mp4' "$LOG_FILE" | tail -1 | sed 's/^Results saved to //')
if [[ -z "$FINAL_FILE" || ! -f "$FINAL_FILE" ]]; then
  echo "ERROR: scripts.inference did not report (or didn't produce) a final output file" >&2
  exit 1
fi
echo "Final output will be: $FINAL_FILE"
