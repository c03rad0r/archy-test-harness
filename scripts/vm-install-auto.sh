#!/usr/bin/env bash
# Install the Archipelago ISO into a fresh 40 GB SATA qcow2, driving the three
# installer prompts through the QEMU monitor.
#
# This is a best-effort wrapper around scripts/vm-boot-installer.sh. The
# installer has no serial console and three Enter prompts; it does not advance
# on its own. If the timing drifts, use --manual and watch the monitor.
#
# Usage: scripts/vm-install-auto.sh --iso PATH [--workdir DIR]
#                                   [--disk-gb 40] [--timeout 1800] [--manual]
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ISO=""
WORK=${VM_WORK:-$HOME/archy-vm}
DISK_GB=40
TIMEOUT=1800
MANUAL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --iso) ISO=${2:?}; shift 2 ;;
    --workdir) WORK=${2:?}; shift 2 ;;
    --disk-gb) DISK_GB=${2:?}; shift 2 ;;
    --timeout) TIMEOUT=${2:?}; shift 2 ;;
    --manual) MANUAL=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -f "$ISO" ] || { echo "FAIL: ISO not found: ${ISO:-<none>}" >&2; exit 1; }
command -v qemu-system-x86_64 >/dev/null || { echo "FAIL: qemu not installed" >&2; exit 1; }
[ -r /dev/kvm ] || { echo "FAIL: /dev/kvm not readable" >&2; exit 1; }
mkdir -p "$WORK"

if [ "$MANUAL" = 1 ]; then
  echo "manual mode: the installer will block at three prompts."
  echo "watch the monitor with:"
  echo "  socat - UNIX-CONNECT:$WORK/monitor.sock"
  echo "and send: sendkey ret"
  exec "$HERE/vm-boot-installer.sh" --iso "$ISO" --disk-gb "$DISK_GB" --timeout "$TIMEOUT"
fi

command -v socat >/dev/null || { echo "FAIL: socat not installed" >&2; exit 1; }

LOG="$WORK/auto-install.log"
rm -f "$WORK/monitor.sock" "$LOG"

echo "starting installer: disk=${DISK_GB}G workdir=$WORK"
VM_WORK="$WORK" "$HERE/vm-boot-installer.sh" \
  --iso "$ISO" --disk-gb "$DISK_GB" --timeout "$TIMEOUT" \
  >"$LOG" 2>&1 &
PID=$!

MON="$WORK/monitor.sock"
for _ in $(seq 1 120); do
  [ -S "$MON" ] && break
  kill -0 "$PID" 2>/dev/null || break
  sleep 1
done

if [ ! -S "$MON" ]; then
  echo "FAIL: QEMU monitor socket did not appear; see $LOG" >&2
  kill "$PID" 2>/dev/null || true
  exit 1
fi

# The three prompt windows are early: isolinux menu, payload found, disk
# confirmation. Extra Enters after install starts are harmless in the TUI.
prev=0
for t in 10 25 50 90 150 240 360; do
  sleep $((t - prev))
  prev=$t
  printf 'sendkey ret\n' | socat - UNIX-CONNECT:"$MON" >/dev/null 2>&1 || true
done

wait "$PID" || true

DISK="$WORK/archy-test.qcow2"
echo
echo "== installer log tail =="
tail -20 "$LOG" 2>/dev/null || true
echo "== disk =="
qemu-img info "$DISK" 2>/dev/null || true

if [ ! -f "$DISK" ]; then
  echo "FAIL: no qcow2 produced" >&2
  exit 1
fi

DISK_BYTES=$(stat -c %s "$DISK")
if [ "$DISK_BYTES" -lt 1000000000 ]; then
  echo "FAIL: disk is only $DISK_BYTES bytes; install likely did not complete" >&2
  exit 1
fi

if grep -qiE 'installation (complete|finished)|install complete|reboot' "$LOG"; then
  echo "RESULT: PASS (installer reported completion)"
else
  echo "RESULT: INCONCLUSIVE (no completion marker in the log; boot it with make vm-run)"
fi
echo "disk: $DISK"
