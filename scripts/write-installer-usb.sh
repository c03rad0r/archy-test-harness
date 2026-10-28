#!/usr/bin/env bash
# Write an Archipelago installer ISO to a USB stick and verify the read-back.
#
# This destroys the target device. It refuses non-USB/removable devices and
# disks larger than 100 GB (the backup HDD).
#
# Usage: scripts/write-installer-usb.sh --iso PATH --device /dev/sdX
#                                       [--sha256 HEX] [--yes]
set -euo pipefail

ORIG_ARGS=("$@")
ISO=""
DEV=""
EXPECTED_SHA=""
ASSUME_YES=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --iso) ISO=${2:?}; shift 2 ;;
    --device) DEV=${2:?}; shift 2 ;;
    --sha256) EXPECTED_SHA=${2:?}; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -f "$ISO" ] || { echo "FAIL: ISO not found: ${ISO:-<none>}" >&2; exit 1; }
[ -b "$DEV" ] || { echo "FAIL: not a block device: ${DEV:-<none>}" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" != 1 ]; then
  exec sudo "$0" "${ORIG_ARGS[@]}"
fi

DEV=$(readlink -f "$DEV")
[ "$(lsblk -dno TYPE "$DEV")" = "disk" ] \
  || { echo "FAIL: $DEV is not a whole disk" >&2; exit 1; }

TRAN=$(lsblk -dno TRAN "$DEV" | tr -d ' ')
RM=$(cat "/sys/block/$(basename "$DEV")/removable" 2>/dev/null || echo 1)
[ "$TRAN" = "usb" ] || [ "$RM" = "1" ] \
  || { echo "FAIL: $DEV is not USB/removable" >&2; exit 1; }

SIZE=$(lsblk -bdno SIZE "$DEV")
[ "$SIZE" -lt 100000000000 ] \
  || { echo "FAIL: refusing a device larger than 100 GB ($DEV)" >&2; exit 1; }

MODEL=$(lsblk -dno MODEL "$DEV" | xargs)
SERIAL=$(lsblk -dno SERIAL "$DEV" | xargs)
ACTUAL_SHA=$(sha256sum "$ISO" | awk '{print $1}')
if [ -n "$EXPECTED_SHA" ] && [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
  echo "FAIL: ISO sha256 $ACTUAL_SHA != expected $EXPECTED_SHA" >&2
  exit 1
fi

mapfile -t MOUNTS < <(lsblk -lnpo MOUNTPOINTS "$DEV" | sed '/^[[:space:]]*$/d')

if [ "$DRY_RUN" = 1 ]; then
  echo "DRY RUN"
  echo "target: $DEV  $MODEL  $SERIAL  $SIZE bytes"
  echo "iso:    $ISO"
  echo "sha256: $ACTUAL_SHA"
  echo "mounted: ${MOUNTS[*]:-none}"
  echo "would write $ISO to $DEV and verify the read-back"
  exit 0
fi

if [ "${#MOUNTS[@]}" -gt 0 ]; then
  if [ "$ASSUME_YES" != 1 ]; then
    echo "FAIL: $DEV is mounted at: ${MOUNTS[*]}" >&2
    echo "Unmount it first, or pass --yes." >&2
    exit 1
  fi
  for mnt in "${MOUNTS[@]}"; do
    echo "unmounting $mnt"
    umount "$mnt"
  done
fi

if [ "$ASSUME_YES" != 1 ]; then
  echo "target: $DEV  $MODEL  $SERIAL  $SIZE bytes"
  read -r -p "Type the device path ($DEV) to destroy it: " ans
  [ "$ans" = "$DEV" ] || { echo "FAIL: confirmation did not match" >&2; exit 1; }
fi

echo "iso:    $ISO"
echo "sha256: $ACTUAL_SHA"
echo "device: $DEV ($MODEL, $SERIAL)"
echo "writing..."
dd if="$ISO" of="$DEV" bs=4M status=progress conv=fsync
sync

ISO_BYTES=$(stat -c %s "$ISO")
echo "verifying $ISO_BYTES bytes..."
cmp -n "$ISO_BYTES" "$ISO" "$DEV" || { echo "FAIL: read-back mismatch" >&2; exit 1; }

echo "RESULT: PASS"
echo "USB is ready. Remove it and boot the test x280 in UEFI mode."
