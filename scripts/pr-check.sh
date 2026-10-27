#!/usr/bin/env bash
# Run the PR evidence matrix for one ref.
#
# Usage: scripts/pr-check.sh --ref BRANCH [--host HOST] [--iso ISO]
#
# The matrix is intentionally the harness's own profiles, not a bespoke test
# list: smoke, critical, media (in-process), media-local (real binary), and
# the ISO artefact when one is supplied.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REF=main
HOST=""
ISO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF=${2:?}; shift 2 ;;
    --host) HOST=${2:?}; shift 2 ;;
    --iso) ISO=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

host_arg=()
[ -n "$HOST" ] && host_arg=(HOST="$HOST")

echo "=== build $REF ==="
make -C "$HERE" build ARCHY_REF="$REF" "${host_arg[@]}"

echo "=== smoke ==="
make -C "$HERE" smoke "${host_arg[@]}"

echo "=== critical ==="
make -C "$HERE" critical "${host_arg[@]}"

echo "=== media (in-process) ==="
make -C "$HERE" media "${host_arg[@]}"

echo "=== media (localnode) ==="
make -C "$HERE" media-local "${host_arg[@]}"

if [ -n "$ISO" ]; then
  echo "=== ISO artefact ==="
  make -C "$HERE" iso-check ARCHY_ISO="$ISO" "${host_arg[@]}"
else
  echo "=== ISO artefact skipped (no --iso) ==="
fi

echo "RESULT: matrix complete"
