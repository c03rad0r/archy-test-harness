#!/usr/bin/env bash
# Build the Archipelago installer ISO on a build host and verify the artefact.
#
# Runs ON the build host. Idempotent in the sense that re-running rebuilds into
# a clean directory; it never mutates the source checkout it is given.
#
# ## Why git archive instead of copying the worktree
# A checked-out worktree carries untracked build output (target/ can be 20 GB+)
# and any local edits. `git archive` gives exactly the committed tree for a ref,
# so the ISO provably corresponds to a commit — which is the whole point of
# recording the SHA in the result.
#
# ## Why verify inside this script
# A build that exits 0 has produced a file, not necessarily a usable installer.
# The checks below are the ones that have actually caught problems: a truncated
# image, a BIOS-only image, and a "successful" build whose rootfs carried the
# wrong daemon version.
#
# Usage:
#   ./build-iso-on-host.sh --repo ~/worktrees/archy-fips-bump [--ref main]
#                          [--workdir ~/archy-build] [--expect-fips 0.5.3]
#                          [--keep-source]
set -uo pipefail
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
export PATH="$HOME/.cargo/bin:$PATH"

REPO=""; REF="main"; WORK=""; EXPECT_FIPS=""
KEEP=0
BUILD_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --repo)        REPO="${2:?}"; shift ;;
        --ref)         REF="${2:?}"; shift ;;
        --workdir)     WORK="${2:?}"; shift ;;
        --expect-fips) EXPECT_FIPS="${2:?}"; shift ;;
        --keep-source) KEEP=1 ;;
        --build-only)  BUILD_ONLY=1 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done
[ -n "$REPO" ] || { echo "FAIL: --repo required" >&2; exit 2; }
[ -d "$REPO/.git" ] || [ -d "$REPO/../.git" ] || { echo "FAIL: not a git repo: $REPO" >&2; exit 1; }
WORK="${WORK:-$HOME/archy-build}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== 0/4 preflight ==="
check_args=()
[ "$BUILD_ONLY" = 1 ] && check_args+=(--build-only)
"$HERE/build-host-check.sh" "${check_args[@]}" || { echo "FAIL: host not fit to build" >&2; exit 1; }

SHA="$(git -C "$REPO" rev-parse "$REF")"
SHORT="$(git -C "$REPO" rev-parse --short "$REF")"
echo "repo: $REPO"
echo "ref:  $REF @ $SHORT"

echo "=== 1/4 materialise the committed tree ==="
SRC="$WORK/src-$SHORT"
rm -rf "$SRC"; mkdir -p "$SRC"
# --work-tree against a live checkout would write into its index; archive never does.
git -C "$REPO" archive "$SHA" | tar -x -C "$SRC" || { echo "FAIL: archive" >&2; exit 1; }
echo "source: $SRC ($(du -sh "$SRC" | cut -f1))"

echo "=== 2/4 build the ISO (this takes ~15-25 min cold) ==="
LOGF="$WORK/build-$SHORT.log"
mkdir -p "$WORK"
set +e
( cd "$SRC" && UNBUNDLED=1 BUILD_FROM_SOURCE=1 bash image-recipe/build-debian-iso.sh ) >"$LOGF" 2>&1
RC=$?
set -e
echo "build rc=$RC (log: $LOGF)"

# The wrapper's own exit code is not proof; a truncated image still exits 0 on
# some failure paths. Fall through to the artefact checks either way.
ISO="$(find "$SRC/image-recipe/results" -maxdepth 1 -name '*.iso' -newermt "-3 hours" 2>/dev/null | head -1)"
[ -n "$ISO" ] || ISO="$(find "$SRC/image-recipe/results" -maxdepth 1 -name '*.iso' 2>/dev/null | head -1)"
[ -n "$ISO" ] || { echo "FAIL: no ISO produced"; echo "RESULT: FAIL"; exit 1; }
echo "iso: $ISO ($(du -h "$ISO" | cut -f1))"

echo "=== 3/4 verify the artefact ==="
FAILED=0
say() { local ok="$1" name="$2" detail="$3"; if [ "$ok" = 1 ]; then echo "PASS: $name — $detail"; else echo "FAIL: $name — $detail"; FAILED=1; fi; }

SIDECAR="$(ls "$SRC"/image-recipe/results/*.sha256 2>/dev/null | head -1)"
if [ -n "$SIDECAR" ]; then
    if ( cd "$(dirname "$ISO")" && sha256sum -c "$SIDECAR" >/dev/null 2>&1 ); then
        say 1 "sha256" "$(sha256sum "$ISO" | cut -c1-16)… matches sidecar"
    else
        say 0 "sha256" "image does not match its .sha256"
    fi
else
    echo "INFO: no .sha256 sidecar to check against"
fi

ET="$(xorriso -indev "$ISO" -report_el_torito as_mkisofs 2>/dev/null || true)"
[ -n "$ET" ] && say 1 "el-torito" "boot record present" || say 0 "el-torito" "no boot record — not bootable"
printf '%s' "$ET" | grep -q "eltorito-alt-boot" && say 1 "uefi" "UEFI boot entry present" || say 0 "uefi" "BIOS-only image"

# The daemon is inside /archipelago/rootfs.tar. Read its dpkg record rather than
# trusting the build log's ARG.
TMPT="$(mktemp -d)"
trap 'rm -rf "$TMPT"' EXIT
if xorriso -osirrox on -indev "$ISO" -extract /archipelago/rootfs.tar "$TMPT/rootfs.tar" >/dev/null 2>&1; then
    VER="$(tar -xOf "$TMPT/rootfs.tar" var/lib/dpkg/status 2>/dev/null | grep -A6 '^Package: fips$' | awk -F': ' '/^Version/{print $2; exit}')"
    if [ -n "$VER" ]; then
        if [ -n "$EXPECT_FIPS" ]; then
            [ "${VER%%-*}" = "${EXPECT_FIPS#v}" ] && say 1 "fips-version" "$VER (expected $EXPECT_FIPS)" \
                                                 || say 0 "fips-version" "$VER but expected $EXPECT_FIPS"
        else
            say 1 "fips-version" "$VER"
        fi
        # The mask is what makes archipelago the sole supervisor; a package unit
        # that is not masked means two daemons race for the same TUN and ports.
        LINK="$(tar -tvf "$TMPT/rootfs.tar" etc/systemd/system/fips.service 2>/dev/null | awk '{print $NF}')"
        case "$LINK" in
            *"/dev/null") say 1 "fips-masked" "fips.service -> /dev/null" ;;
            *)            say 0 "fips-masked" "fips.service is not masked (${LINK:-absent})" ;;
        esac
    else
        say 0 "fips-version" "package 'fips' absent from rootfs"
    fi
else
    say 0 "rootfs" "could not extract /archipelago/rootfs.tar"
fi

echo "=== 4/4 result ==="
echo "commit:  $SHA"
echo "iso:     $ISO"
echo "log:     $LOGF"
printf '{"commit":"%s","iso":"%s","build_rc":%d,"result":"%s"}\n' \
    "$SHA" "$ISO" "$RC" "$([ "$FAILED" = 0 ] && echo PASS || echo FAIL)" \
    | tee "$WORK/result-$SHORT.json"

[ "$KEEP" = 1 ] || echo "note: source tree kept at $SRC (re-run with --keep-source to keep it clean)"
[ "$FAILED" = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit "$FAILED"
