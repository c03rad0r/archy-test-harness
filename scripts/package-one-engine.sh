#!/usr/bin/env bash
# Package the one-engine test artifacts:
#   <out>/archipelago                    stripped backend binary
#   <out>/neode-ui/                      built Vue bundle
#   <out>/tollgate-installer-linux-amd64 (if present on the SUT/controller)
#
# The binary/UI are read from the SUT worktree recorded in sut.env. With
# --host, the SUT lives on that fleet host and artifacts are rsynced back.
set -euo pipefail

HOST=""
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=${2:?}; shift 2 ;;
    --out) OUT=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${OUT:-"$REPO/artifacts"}
mkdir -p "$OUT"

package_local() {
  local sut=$1
  [ -n "$sut" ] || { echo "FAIL: no ARCHY_SUT" >&2; exit 1; }
  local bin="$sut/core/target/debug/archipelago"
  [ -f "$bin" ] || { echo "FAIL: missing $bin" >&2; exit 1; }
  cp "$bin" "$OUT/archipelago"
  strip --strip-all "$OUT/archipelago" 2>/dev/null || true
  rm -rf "$OUT/neode-ui"
  mkdir -p "$OUT/neode-ui"
  cp -a "$sut/web/dist/neode-ui/." "$OUT/neode-ui/"
  if [ -f "$sut/../tollgate-installer-linux-amd64" ]; then
    cp "$sut/../tollgate-installer-linux-amd64" "$OUT/tollgate-installer-linux-amd64"
  fi
}

if [ -n "$HOST" ]; then
  "$REPO/scripts/remote.sh" "$HOST" '
    set -e
    SUT=$(sed -n "s/^ARCHY_SUT=//p" "$HOME/.cache/archy-test-harness/sut.env" | tail -1)
    [ -n "$SUT" ] || { echo "FAIL: no ARCHY_SUT in ~/.cache/archy-test-harness/sut.env" >&2; exit 1; }
    BIN="$SUT/core/target/debug/archipelago"
    [ -f "$BIN" ] || { echo "FAIL: missing $BIN" >&2; exit 1; }
    cp "$BIN" /tmp/archipelago.one-engine
    strip --strip-all /tmp/archipelago.one-engine 2>/dev/null || true
    tar -C "$SUT/web/dist" -czf /tmp/neode-ui.tgz neode-ui
    ls -lh /tmp/archipelago.one-engine /tmp/neode-ui.tgz
  '
  rsync -av --partial "$HOST:/tmp/archipelago.one-engine" "$OUT/archipelago"
  rsync -av --partial "$HOST:/tmp/neode-ui.tgz" "$OUT/neode-ui.tgz"
  rm -rf "$OUT/neode-ui"
  tar -xzf "$OUT/neode-ui.tgz" -C "$OUT"
  rm -f "$OUT/neode-ui.tgz"
  # The installer binary is built separately; copy it if it is sitting next to
  # the local checkout or in the artifacts dir already.
  if [ -f "$HOME/archy-bin/tollgate-installer-linux-amd64" ]; then
    cp "$HOME/archy-bin/tollgate-installer-linux-amd64" "$OUT/tollgate-installer-linux-amd64"
  fi
else
  SUT=${ARCHY_SUT:-}
  if [ -z "$SUT" ] && [ -r "$HOME/.cache/archy-test-harness/sut.env" ]; then
    SUT=$(sed -n 's/^ARCHY_SUT=//p' "$HOME/.cache/archy-test-harness/sut.env" | tail -1)
  fi
  package_local "$SUT"
fi

echo "== artifacts =="
find "$OUT" -maxdepth 1 -type f -printf '%f %s bytes\n' | sort
