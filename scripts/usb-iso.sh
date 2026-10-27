#!/usr/bin/env bash
# Copy a built Archipelago ISO onto an existing Ventoy stick and add a menu alias.
#
# This does not create or wipe the Ventoy stick. Prepare that with
# kali-live-headless:
#   make -C ~/kali-live-headless ventoy-all USB=/dev/sdc CONFIRM=yes
#
# Usage: scripts/usb-iso.sh --iso PATH [--stick /dev/sdc]
#                           [--kali-repo DIR] [--alias NAME]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

ISO=""
STICK=${STICK:-/dev/sdc}
KALI=${KALI_REPO:-}
ALIAS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --iso) ISO=${2:?}; shift 2 ;;
    --stick) STICK=${2:?}; shift 2 ;;
    --kali-repo) KALI=${2:?}; shift 2 ;;
    --alias) ALIAS=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -f "$ISO" ] || { echo "FAIL: ISO not found: ${ISO:-<none>}" >&2; exit 1; }
[ -b "$STICK" ] || { echo "FAIL: not a block device: $STICK" >&2; exit 1; }

PART=$(lsblk -lnpo NAME,FSTYPE,LABEL "$STICK" | awk '$2 == "exfat" && $3 == "Ventoy" { print $1; exit }')
if [ -z "$PART" ]; then
  echo "FAIL: no exFAT 'Ventoy' partition on $STICK." >&2
  echo "Prepare it first with kali-live-headless:" >&2
  echo "  make -C ${KALI:-~/kali-live-headless} ventoy-all USB=$STICK CONFIRM=yes" >&2
  exit 1
fi

MNT=$(mktemp -d /mnt/ventoy-iso.XXXXXX)
cleanup() {
  umount "$MNT" 2>/dev/null || true
  rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

mount -t exfat "$PART" "$MNT"
NAME=$(basename "$ISO")
DEST="$MNT/$NAME"
echo "copying $ISO -> $DEST"
cp --no-preserve=mode,ownership "$ISO" "$DEST"
sync

SRC_HASH=$(sha256sum "$ISO" | awk '{print $1}')
DST_HASH=$(sha256sum "$DEST" | awk '{print $1}')
[ "$SRC_HASH" = "$DST_HASH" ] || { echo "FAIL: hash mismatch after copy" >&2; exit 1; }

ALIAS=${ALIAS:-"$(basename "$NAME" .iso)"}
mkdir -p "$MNT/ventoy"
python3 - "$MNT/ventoy/ventoy.json" "/$NAME" "$ALIAS" <<'PY'
import json
import os
import sys

path, image, alias = sys.argv[1], sys.argv[2], sys.argv[3]
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
aliases = data.setdefault("menu_alias", [])
if not any(entry.get("image") == image for entry in aliases):
    aliases.append({"image": image, "alias": alias})
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY

sync
echo "ok: $NAME"
echo "sha256: $SRC_HASH"
echo "alias: $ALIAS"
echo "Ventoy rescans the data partition at boot; no reinstall needed."
