#!/usr/bin/env bash
# Build the neode-ui bundle for the one-engine test lane.
#
# The harness `make build` target builds the Rust backend only. This script
# runs the Vue build in the same SUT worktree, either locally or on HOST.
set -euo pipefail

HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SUT_ENV="$HOME/.cache/archy-test-harness/sut.env"

if [ -n "$HOST" ]; then
  "$REPO/scripts/remote.sh" "$HOST" '
    set -e
    SUT=$(sed -n "s/^ARCHY_SUT=//p" "$HOME/.cache/archy-test-harness/sut.env" | tail -1)
    [ -n "$SUT" ] || { echo "FAIL: no ARCHY_SUT in ~/.cache/archy-test-harness/sut.env" >&2; exit 1; }
    cd "$SUT/neode-ui"
    npm run build
  '
  exit 0
fi

SUT=${ARCHY_SUT:-}
if [ -z "$SUT" ] && [ -r "$SUT_ENV" ]; then
  SUT=$(sed -n 's/^ARCHY_SUT=//p' "$SUT_ENV" | tail -1)
fi
[ -n "$SUT" ] || { echo "FAIL: set ARCHY_SUT or pass --host" >&2; exit 1; }
cd "$SUT/neode-ui"
npm run build
