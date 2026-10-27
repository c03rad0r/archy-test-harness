#!/usr/bin/env bash
# Boot an ALREADY-INSTALLED Archipelago disk with port forwards, so a browser on
# another host can drive its UI without a console.
#
# Why this exists separately from vm-boot-installer.sh:
#   * that script boots the ISO to install; this one boots the result of installing
#   * the installed system needs forwarded ports (22 for the harness shell, 80 for
#     the web UI) which the installer run has no use for
#
# The forwards bind to 127.0.0.1 on the VM host on purpose. The guest is an
# unauthenticated-LAN machine by default; publishing its ports on 0.0.0.0 would
# expose a half-onboarded node to whatever shares the network. Reach it through an
# SSH tunnel instead (scripts/run-onboarding-e2e.sh does exactly this).
#
# Usage: vm-boot-installed.sh --disk <qcow2> [--ram 2048] [--cpus 2]
#                             [--ssh-port 2222] [--web-port 8080] [--workdir DIR]
set -uo pipefail

DISK=""; RAM=2048; CPUS=2; SSH_PORT=2222; WEB_PORT=8080
while [ $# -gt 0 ]; do
    case "$1" in
        --disk)     DISK="${2:?}"; shift ;;
        --ram)      RAM="${2:?}"; shift ;;
        --cpus)     CPUS="${2:?}"; shift ;;
        --ssh-port) SSH_PORT="${2:?}"; shift ;;
        --web-port) WEB_PORT="${2:?}"; shift ;;
        --workdir)  VM_WORK="${2:?}"; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

[ -f "$DISK" ] || { echo "FAIL: no such disk: ${DISK:-<none>}" >&2; exit 1; }
command -v qemu-system-x86_64 >/dev/null || { echo "FAIL: qemu not installed" >&2; exit 1; }
[ -r /dev/kvm ] || { echo "FAIL: /dev/kvm not readable" >&2; exit 1; }

WORK="${VM_WORK:-$HOME/archy-vm}"
mkdir -p "$WORK"
MON="$WORK/monitor-guest.sock"
rm -f "$MON"

echo "disk:  $DISK"
echo "ssh:   ssh -p $SSH_PORT root@127.0.0.1   (via the VM host)"
echo "web:   http://127.0.0.1:$WEB_PORT/"
echo "mon:   $MON"

QEMU_ARGS=(
    -enable-kvm -machine q35 -cpu host -smp "$CPUS" -m "$RAM"
    -drive "file=$DISK,format=qcow2,if=ide,cache=writeback"
    -boot order=c
    -display none
    -monitor "unix:$MON,server,nowait"
    -serial "file:$WORK/serial-installed.log"
    -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:${WEB_PORT}-:80"
    -device "e1000,netdev=n0"
)

# Foreground by design: the caller supervises it (background + teardown), so the
# script never has to guess when it is safe to kill a VM someone else is using.
exec qemu-system-x86_64 "${QEMU_ARGS[@]}"
