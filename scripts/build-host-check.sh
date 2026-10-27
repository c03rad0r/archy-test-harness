#!/usr/bin/env bash
# Verify a machine is fit to be an Archipelago ISO build host, and say exactly
# what is missing. Read-only: this script never installs anything.
#
# Why a separate check: the ISO build is a 25-minute, multi-GB, Rust-compiling
# job. Discovering a missing binary or an unreadable /dev/kvm at minute 20 wastes
# the run — and on a box with a daily reset window, wastes the whole day.
#
# Exit 0 = fit. Exit 1 = not fit, with one FAIL line per missing requirement.
#
# Usage: build-host-check.sh [--json]
set -uo pipefail
export PATH="$HOME/.cargo/bin:$PATH"

JSON=0
BUILD_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --json) JSON=1; shift ;;
        --build-only) BUILD_ONLY=1; shift ;;
        -h|--help) echo "usage: build-host-check.sh [--json] [--build-only]"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
FAILED=0
declare -a REQS=()

req() {
    local name="$1" ok="$2" detail="$3"
    if [ "$ok" = "1" ]; then
        REQS+=("PASS|$name|$detail")
    else
        REQS+=("FAIL|$name|$detail")
        FAILED=1
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# --- tooling ---------------------------------------------------------------
for tool in podman xorriso debootstrap qemu-img isolinux mksquashfs grub-mkrescue; do
    req "tool:$tool" "$(have "$tool" && echo 1 || echo 0)" "$(have "$tool" && command -v "$tool" || echo 'not installed')"
done

# qemu-system-x86_64 is only needed if VMs run here; it is not required for a
# pure build host, so it is reported but does not fail the check.
QSYS="$(have qemu-system-x86_64 && echo 1 || echo 0)"
REQS+=("INFO|tool:qemu-system-x86_64|$([ "$QSYS" = 1 ] && command -v qemu-system-x86_64 || echo 'absent (fine for build-only)')")

# --- kernel / virtualisation ----------------------------------------------
KVMO="$( [ -r /dev/kvm ] && echo 1 || echo 0 )"
if [ "$BUILD_ONLY" = 1 ]; then
    REQS+=("INFO|kvm:readable|not required for a build-only host")
else
    req "kvm:readable" "$KVMO" "$([ "$KVMO" = 1 ] && echo '/dev/kvm readable' || echo '/dev/kvm not readable — VMs cannot run here')"
fi

# --- capacity --------------------------------------------------------------
CORES="$(nproc 2>/dev/null || echo 0)"
if [ "$BUILD_ONLY" = 1 ]; then
    CPU_MIN=2
    CPU_LABEL="cpu:cores>=2"
else
    CPU_MIN=8
    CPU_LABEL="cpu:cores>=8"
fi
req "$CPU_LABEL" "$( [ "$CORES" -ge "$CPU_MIN" ] && echo 1 || echo 0 )" "${CORES} cores"

MEM_MB="$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
# A cold Rust build of the backend needs real RAM; below ~7 GB the linker starts
# swapping and builds get SIGKILLed (observed on a 7.4 GB host).
req "mem:>=7000MB" "$( [ "$MEM_MB" -ge 7000 ] && echo 1 || echo 0 )" "${MEM_MB} MB"

DISK_GB="$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')"
# Budget: ISO 2.6 GB + worktree/target ~15 GB + a second ISO during verification.
if [ "$BUILD_ONLY" = 1 ]; then
    DISK_MIN=30
else
    DISK_MIN=40
fi
req "disk:avail>=${DISK_MIN}GB" "$( [ "${DISK_GB:-0}" -ge "$DISK_MIN" ] && echo 1 || echo 0 )" "${DISK_GB:-0} GB free on /"

# --- container runtime -----------------------------------------------------
if have podman; then
    ROOTLESS="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo unknown)"
    REQS+=("INFO|podman:rootless|$ROOTLESS")
    # Unqualified FROM lines need a search registry configured, or every build
    # dies at step 1 with "short-name did not resolve".
    if grep -rqs "unqualified-search-registries" /etc/containers/registries.conf \
         /etc/containers/registries.conf.d/ 2>/dev/null; then
        req "podman:search-registries" 1 "configured"
    else
        req "podman:search-registries" 0 "unqualified-search-registries not set — 'FROM rust:...' will fail"
    fi
fi

# --- report ----------------------------------------------------------------
if [ "$JSON" = 1 ]; then
    printf '{'
    first=1
    for r in "${REQS[@]}"; do
        IFS='|' read -r st name detail <<< "$r"
        [ "$first" = 1 ] || printf ','
        first=0
        printf '"%s":{"status":"%s","detail":"%s"}' "$name" "$st" "$detail"
    done
    printf ',"result":"%s"}\n' "$([ "$FAILED" = 0 ] && echo PASS || echo FAIL)"
else
    for r in "${REQS[@]}"; do
        IFS='|' read -r st name detail <<< "$r"
        printf '%-5s %-26s %s\n' "$st" "$name" "$detail"
    done
    echo
    if [ "$FAILED" = 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL"; fi
fi
exit "$FAILED"
