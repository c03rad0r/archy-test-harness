#!/usr/bin/env bash
# Allow key-only root SSH on a disposable test VM and make sure sshd starts.
#
# PermitRootLogin is set to `prohibit-password`, never `yes`: password auth as
# root stays off, only the injected key works. And the account is root only
# because the harness needs a shell to run fipsctl/systemctl as root; this is a
# throwaway VM, not a deployment posture.
set -uo pipefail

DISK="${1:?usage: enable-ssh-root.sh <disk.qcow2> [mountpoint]}"
MNT="${2:-/mnt/vmroot}"
NBD=/dev/nbd1

if pgrep -f "qemu[-]system.*$(basename "$DISK")" >/dev/null 2>&1; then
    echo "FAIL: $DISK is in use by a running VM" >&2
    exit 1
fi

cleanup() {
    sudo umount "$MNT" 2>/dev/null || true
    sudo qemu-nbd -d "$NBD" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo -n modprobe nbd max_part=8
sudo -n qemu-nbd -c "$NBD" "$DISK" || { echo "FAIL: qemu-nbd attach" >&2; exit 1; }
sleep 2
sudo -n mkdir -p "$MNT"
sudo -n mount "${NBD}p3" "$MNT" || { echo "FAIL: mount" >&2; exit 1; }

CFG="$MNT/etc/ssh/sshd_config"
if [ ! -f "$CFG" ]; then
    echo "FAIL: no sshd_config in guest — openssh-server is not installed" >&2
    exit 1
fi

# Rewrite the directive in place rather than appending: a later `PermitRootLogin
# no` in the file would win over an appended line, and sshd uses the FIRST
# value it sees for most keywords — so an append is not reliable either way.
sudo -n sed -i -E 's/^[[:space:]]*#?[[:space:]]*PermitRootLogin[[:space:]].*/PermitRootLogin prohibit-password/' "$CFG"
sudo -n sed -i -E 's/^[[:space:]]*#?[[:space:]]*PubkeyAuthentication[[:space:]].*/PubkeyAuthentication yes/' "$CFG"
sudo -n grep -nE "^PermitRootLogin|^PubkeyAuthentication" "$CFG"

# ssh.service ships as ssh.service (Debian). Enable by symlink because systemctl
# cannot run against an offline root without a running systemd.
UNIT=""
for candidate in ssh.service sshd.service; do
    if [ -f "$MNT/lib/systemd/system/$candidate" ] || [ -f "$MNT/usr/lib/systemd/system/$candidate" ]; then
        UNIT="$candidate"; break
    fi
done
if [ -z "$UNIT" ]; then
    echo "FAIL: no ssh.service unit found in guest" >&2
    exit 1
fi
sudo -n mkdir -p "$MNT/etc/systemd/system/multi-user.target.wants"
sudo -n ln -sf "/lib/systemd/system/$UNIT" "$MNT/etc/systemd/system/multi-user.target.wants/$UNIT"
echo "enabled unit: $UNIT"

echo "SSH_READY"
