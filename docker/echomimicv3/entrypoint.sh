#!/usr/bin/env bash
set -euo pipefail

if [ -n "${PUBLIC_KEY:-}" ]; then
  mkdir -p /root/.ssh
  echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh
  chmod 600 /root/.ssh/authorized_keys
fi
/usr/sbin/sshd

# Batch-job container: no HTTP server. Run scripts/run_echomimicv3.sh
# manually or via scripts/generate_witch_video.py (which uploads it fresh
# per job -- same reasoning as docker/musetalk/entrypoint.sh, now deleted).
if [ "$#" -eq 0 ]; then
  exec sleep infinity
else
  exec "$@"
fi
