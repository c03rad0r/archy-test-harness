#!/usr/bin/env bash
# Build the Archipelago binary from a fork ref in a dedicated worktree.
#
# Usage: scripts/build-sut.sh [--sut DIR] [--ref REF] [--repo URL]
#                             [--worktree DIR] [--release]
#
# Writes ~/.cache/archy-test-harness/sut.env with ARCHY_SUT and ARCHY_BINARY
# so `make test` can pick up the build. Never mutates the caller's checkout.
set -euo pipefail

SUT=${ARCHY_SUT:-$HOME/archy-fork}
REF=${ARCHY_REF:-main}
REPO_URL=${ARCHY_REPO_URL:-https://github.com/felixfelix-bot/archy-fork.git}
RELEASE=0
WORKTREE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --sut) SUT=${2:?}; shift 2 ;;
    --ref) REF=${2:?}; shift 2 ;;
    --repo) REPO_URL=${2:?}; shift 2 ;;
    --worktree) WORKTREE=${2:?}; shift 2 ;;
    --release) RELEASE=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo >/dev/null || { echo "cargo not found; run make host-bootstrap HOST=..." >&2; exit 1; }
command -v git >/dev/null || { echo "git not found" >&2; exit 1; }

if [ ! -d "$SUT/.git" ]; then
  echo "cloning $REPO_URL -> $SUT"
  mkdir -p "$(dirname "$SUT")"
  git clone "$REPO_URL" "$SUT"
fi

git -C "$SUT" fetch --all --tags --prune

slug=$(printf '%s' "$REF" | tr '/:' '--')
WORKTREE=${WORKTREE:-$HOME/worktrees/archy-$slug}

if git -C "$SUT" rev-parse --verify --quiet "origin/$REF" >/dev/null; then
  TARGET="origin/$REF"
else
  TARGET="$REF"
fi

if [ -d "$WORKTREE/.git" ]; then
  git -C "$WORKTREE" fetch --all --tags --prune
  git -C "$WORKTREE" checkout --detach "$TARGET"
else
  mkdir -p "$(dirname "$WORKTREE")"
  git -C "$SUT" worktree add --detach "$WORKTREE" "$TARGET"
fi

echo "building $REF ($TARGET) in $WORKTREE"
cd "$WORKTREE/core"

build_args=(build -p archipelago --bin archipelago)
if [ "$RELEASE" = 1 ]; then
  build_args+=(--release)
fi

cargo "${build_args[@]}"

PROFILE_DIR=debug
[ "$RELEASE" = 1 ] && PROFILE_DIR=release
BIN="$WORKTREE/core/target/$PROFILE_DIR/archipelago"
if [ ! -x "$BIN" ]; then
  BIN=$(find "$WORKTREE/core/target" -path "*/$PROFILE_DIR/archipelago" -type f -perm -u+x | head -1)
fi
[ -n "${BIN:-}" ] && [ -x "$BIN" ] || { echo "build succeeded but no binary found" >&2; exit 1; }

CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/archy-test-harness
mkdir -p "$CACHE"
{
  printf 'export ARCHY_SUT=%q\n' "$WORKTREE"
  printf 'export ARCHY_BINARY=%q\n' "$BIN"
  printf 'export ARCHY_REF=%q\n' "$REF"
} > "$CACHE/sut.env"

echo "ARCHY_SUT=$WORKTREE"
echo "ARCHY_BINARY=$BIN"
echo "env file: $CACHE/sut.env"
