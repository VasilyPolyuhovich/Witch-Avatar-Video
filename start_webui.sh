#!/usr/bin/env bash
# One-command build + run for the WebUI -- no RunPod/CLI knowledge needed.
# Usage: ./start_webui.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Witch Avatar Video: WebUI setup ==="

if [ ! -d .venv ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi

echo "Installing dependencies (first run can take a few minutes)..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

KEY_FILE="$HOME/.runpod-key-witch-video"
if [ ! -f "$KEY_FILE" ]; then
  echo ""
  echo "No RunPod API key found."
  echo "Get one at https://www.runpod.io/console/user/settings (API Keys section)."
  read -rp "Paste your RunPod API key: " RUNPOD_KEY_INPUT
  printf '%s' "$RUNPOD_KEY_INPUT" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "Saved to $KEY_FILE"
fi

SSH_KEY="$HOME/.runpod/ssh/runpodctl-witch-video-ssh-key"
if [ ! -f "${SSH_KEY}.pub" ]; then
  echo "Generating an SSH keypair for pod access (one-time)..."
  mkdir -p "$(dirname "$SSH_KEY")"
  ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
fi

export WEBUI_BACKEND_IMAGE="${WEBUI_BACKEND_IMAGE:-ghcr.io/vasilypolyuhovich/witch-avatar-echomimicv3-webui:latest}"
export ACCOUNT_KEY_FILE="$KEY_FILE"
export SSH_PUBKEY_FILE="${SSH_KEY}.pub"

LOG_FILE="$HOME/.witch-avatar-webui.log"
echo ""
echo "Starting WebUI at http://localhost:8080 ..."
echo "Press Ctrl-C to stop. Full log also saved to $LOG_FILE"
echo ""

( sleep 2; command -v open >/dev/null 2>&1 && open "http://localhost:8080" || true ) &

# Not `exec` -- piping through tee (for a persistent log alongside the
# terminal) needs this process to stay alive to read uvicorn's output.
# `set +e` around the pipeline: under `pipefail` (set at the top of this
# script) a non-zero uvicorn exit would otherwise trigger `set -e` and
# exit immediately, before PIPESTATUS could be read below to report it.
set +e
.venv/bin/uvicorn webui.backend.app:app --port 8080 --host 127.0.0.1 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  echo ""
  echo "*** WebUI backend exited with an error (code $rc). See $LOG_FILE for the full log. ***" >&2
  exit "$rc"
fi
