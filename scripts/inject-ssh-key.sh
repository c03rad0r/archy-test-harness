#!/usr/bin/env bash
# Inject an SSH key into a disposable Archipelago VM disk and report what the
# guest serves, so a harness can drive its UI without a console.
#
# This modifies the VM disk on purpose. That is acceptable here because the disk
# is a throwaway created by scripts/vm-boot-installer.sh for this test; it is NOT
# a general-purpose tool and refuses to run against a disk that is in use.
#
# Usage: inject-ssh-key.sh <disk.qcow2> <pubkey-file> [root-mount]
set -uo pipefail

DISK="${1:?usage: inject-ssh-key.sh <disk.qcow2> <pubkey> [mountpoint]}"
PUBKEY="${2:?usage: inject-ssh-key.sh <disk.qcow2> <pubkey> [mountpoint]}"
MNT="${3:-/mnt/vmroot}"
NBD=/dev/nbd1

[ -f "$DISK" ] || { echo "FAIL: no such disk: $DISK" >&2; exit 1; }
[ -f "$PUBKEY" ] || { echo "FAIL: no such pubkey: $PUBKEY" >&2; exit 1; }

# A disk in use by a running VM must not be touched: writing under a live guest
# corrupts the filesystem in a way that looks like a mysterious boot failure.
#
# The pattern uses `qemu[-]system`, not `qemu-system`: a literal pattern would
# also match THIS script's own command line (which contains the disk name), and
# the guard would refuse to run on an idle disk. Bracket-escaping makes the
# shell's own cmdline not match the regex it is running.
if pgrep -f "qemu[-]system.*$(basename "$DISK")" >/dev/null 2>&1; then
    echo "FAIL: $DISK is in use by a running VM (stop it first)" >&2
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
# rw (not ro,noload): the journal must replay, or writes land in an inconsistent
# filesystem. This disk is disposable, so replay is safe and correct.
sudo -n mount "${NBD}p3" "$MNT" || { echo "FAIL: mount ${NBD}p3" >&2; exit 1; }

echo "=== guest identity ==="
sudo -n chroot "$MNT" /bin/sh -c 'cat /etc/hostname 2>/dev/null' || true

echo "=== openssh-server present? ==="
if [ -x "$MNT/usr/sbin/sshd" ]; then echo "sshd: yes"; else echo "sshd: NO"; fi

echo "=== web UI listeners declared by units ==="
sudo -n grep -rhoE "ExecStart=[^ ]*" "$MNT/etc/systemd/system/" 2>/dev/null | head -10
sudo -n ls "$MNT/etc/systemd/system/multi-user.target.wants/" 2>/dev/null | head -20

echo "=== install the key for root ==="
sudo -n mkdir -p "$MNT/root/.ssh"
sudo -n cp "$PUBKEY" "$MNT/root/.ssh/authorized_keys"
sudo -n chmod 700 "$MNT/root/.ssh"
sudo -n chmod 600 "$MNT/root/.ssh/authorized_keys"
sudo -n chown -R 0:0 "$MNT/root/.ssh" 2>/dev/null || true
# 0:0 is literal in the guest; chown by name needs the guest's passwd db.
sudo -n chroot "$MNT" /bin/sh -c 'chown -R root:root /root/.ssh' 2>/dev/null || true

echo "=== sshd_config: permit root by key ==="
if [ -f "$MNT/etc/ssh/sshd_config" ]; then
    sudo -n grep -nE "^#?PermitRootLogin|^#?PubkeyAuthentication" "$MNT/etc/ssh/sshd_config" | head -4
fi

echo "INJECT_DONE"
