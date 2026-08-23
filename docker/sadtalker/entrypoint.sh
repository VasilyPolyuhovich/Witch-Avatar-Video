#!/usr/bin/env bash
set -euo pipefail

if [ -n "${PUBLIC_KEY:-}" ]; then
  mkdir -p /root/.ssh
  echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh
  chmod 600 /root/.ssh/authorized_keys
fi
/usr/sbin/sshd

# Batch-job container: no HTTP server. Keep PID 1 alive over SSH and run
# scripts/run_sadtalker.sh manually or via scripts/generate_witch_video.py
# (which uploads that script fresh per job -- see its docstring for why),
# or pass a command to run non-interactively.
if [ "$#" -eq 0 ]; then
  exec sleep infinity
else
  exec "$@"
fi
