#!/usr/bin/env bash
# Sync the harness to a fleet host and run a command there.
#
# Usage: scripts/remote.sh HOST 'make smoke'
#
# The SUT is not synced. It is expected at ~/archy-fork on the host, or at
# ARCHY_SUT if the remote make is given that variable.
set -euo pipefail

HOST=${1:?usage: remote.sh HOST 'command'}
shift
[ $# -gt 0 ] || { echo "remote.sh: command required" >&2; exit 2; }

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEST=${REMOTE_DIR:-archy-test-harness}

command -v rsync >/dev/null || { echo "rsync is required locally" >&2; exit 1; }

rsync -az --delete \
  --exclude .git \
  --exclude results \
  --exclude .venv \
  --exclude venv \
  --exclude node_modules \
  --exclude .pytest_cache \
  --exclude __pycache__ \
  "$REPO/" "$HOST:$DEST/"

ssh "$HOST" "cd ~/$DEST && $*"
