#!/usr/bin/env bash
# One command: boot an installed Archipelago disk, drive first-run onboarding in a
# real browser, verify the FIPS mesh came up, and tear everything down.
#
# The three facts this proves, which no unit test can:
#   1. the installed image boots and serves its UI
#   2. onboarding completes through a real browser and creates the identity key
#   3. that key is what starts the daemon, which then forms an authenticated peer
#
# Split across two machines by necessity: the VM runs on a KVM host, the browser
# runs where Playwright is installed. Both are configured, not assumed.
#
# Usage:
#   ./run-onboarding-e2e.sh \
#     --host c08r4d0r@fd59:2944:3a9e:9de7:3081:b7d7:e25d:e489 \
#     --disk /home/c08r4d0r/archy-vm/archy-test.qcow2 \
#     --password 'TestNode-2026-archy'
#
# Requirements:
#   * SSH key access to --host, and sudo -n on it (qemu + nbd)
#   * Playwright resolvable by node FROM THIS DIRECTORY (see docs/ONBOARDING-E2E.md)
#   * /dev/kvm on the host; a disk produced by vm-boot-installer.sh + onboarding
set -uo pipefail

HOST=""; DISK=""; PASSWORD=""; INJECT_KEY=""
SSH_KEY="${HOME}/.ssh/id_fleet"
SSH_HOST=""
SSH_PORT=2222; WEB_PORT=8080; LOCAL_WEB=18080
RAM=2048; CPUS=2
EXPECT="${ARCHY_FIPS_VERSION:-0.5.3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
    case "$1" in
        --host)     HOST="${2:?}"; shift ;;
        --disk)     DISK="${2:?}"; shift ;;
        --password) PASSWORD="${2:?}"; shift ;;
        --inject-key) INJECT_KEY="${2:?}"; shift ;;
        --ssh-key)  SSH_KEY="${2:?}"; shift ;;
        --ssh-port) SSH_PORT="${2:?}"; shift ;;
        --web-port) WEB_PORT="${2:?}"; shift ;;
        --local-web) LOCAL_WEB="${2:?}"; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

[ -n "$HOST" ] || { echo "FAIL: --host required" >&2; exit 2; }
[ -n "$DISK" ] || { echo "FAIL: --disk required" >&2; exit 2; }
[ -n "$PASSWORD" ] || { echo "FAIL: --password required (sets the node admin password in the VM)" >&2; exit 2; }
command -v node >/dev/null || { echo "FAIL: node not installed" >&2; exit 1; }

SSH_HOST="${HOST#*@}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
PROXY="ssh -o StrictHostKeyChecking=no -W %h:%p $HOST"
if [ -n "$SSH_KEY" ]; then
    [ -f "$SSH_KEY" ] || { echo "FAIL: no such key: $SSH_KEY" >&2; exit 1; }
    SSH_OPTS+=(-i "$SSH_KEY")
    PROXY="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -W %h:%p $HOST"
fi

# The guest is a throwaway VM reached through a localhost port forward; its SSH
# host key changes on every reinstall, so verifying it would fail every run for a
# reason that carries no security meaning here. This exemption is scoped to the
# guest ONLY — the hop to the VM host keeps normal host-key checking.
GUEST_SSH_OPTS=("${SSH_OPTS[@]}" -o UserKnownHostsFile=/dev/null)

# -n keeps a probe from consuming this script's stdin. It is deliberately NOT
# used by rssh_put: `ssh -n` redirects stdin from /dev/null, so `cat > file <
# local` silently writes a ZERO-BYTE file. That bug shipped once (a 0-byte boot
# script) and only looked fine because stale copies were already on the host.
rssh() { ssh -n "${SSH_OPTS[@]}" "$HOST" "$@"; }

# Copy a local file to the host at an absolute path, preserving +x.
rssh_put() {
    local dest="$1" src="$2"
    ssh "${SSH_OPTS[@]}" "$HOST" "cat > '$dest' && chmod +x '$dest'" < "$src"
    ssh -n "${SSH_OPTS[@]}" "$HOST" "test -s '$dest'" \
        || { echo "FAIL: upload of $src to $dest produced an empty file" >&2; exit 1; }
}

VM_STARTED=0; TUNNEL_PID=""

cleanup() {
    [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null
    if [ "$VM_STARTED" = "1" ]; then
        echo "--- teardown: stopping VM on $SSH_HOST ---"
        # Kill by PID, never `pkill -f <pattern>`: the pattern appears in the
        # remote shell's own command line and would kill the session instead.
        # `qemu[-]system` keeps the pattern from matching its own invocation.
        rssh "for p in \$(pgrep -f '$DISK_PAT'); do kill \"\$p\"; done" 2>/dev/null
    fi
}
trap cleanup EXIT INT TERM

echo "=== 1/5 preflight ==="
rssh "test -f '$DISK'" || { echo "FAIL: $DISK not on $SSH_HOST" >&2; exit 1; }
rssh "test -r /dev/kvm" || { echo "FAIL: /dev/kvm not readable on $SSH_HOST" >&2; exit 1; }
rssh "command -v qemu-system-x86_64 >/dev/null" || { echo "FAIL: qemu missing on $SSH_HOST" >&2; exit 1; }
# A disk already held by a VM must not be booted twice. Match the FULL path: two
# runs of vm-boot-installer.sh both produce `archy-test.qcow2`, so a basename
# match reports the wrong disk as busy (observed: a live VM on ~/archy-vm while
# ~/archy-vm2 was idle).
DISK_PAT="qemu[-]system.*${DISK}"
if rssh "pgrep -f '$DISK_PAT' >/dev/null"; then
    echo "FAIL: a VM is already using $DISK on $SSH_HOST — stop it first" >&2
    exit 1
fi
echo "preflight ok"

# A freshly installed disk has no SSH at all: openssh-server is present but root
# login is disabled and no key exists. Without this step onboarding is unreachable
# and the lane could only ever run against a disk someone had already prepared by
# hand — which is not a test.
if [ -n "$INJECT_KEY" ]; then
    [ -f "$INJECT_KEY" ] || { echo "FAIL: no such pubkey: $INJECT_KEY" >&2; exit 1; }
    echo "=== injecting SSH access into the disk (pre-boot) ==="
    rssh_put /tmp/harness.pub "$INJECT_KEY"
    rssh_put /tmp/harness-inject.sh "$HERE/inject-ssh-key.sh"
    rssh_put /tmp/harness-enable-ssh.sh "$HERE/enable-ssh-root.sh"
    rssh "/tmp/harness-inject.sh '$DISK' /tmp/harness.pub"  | tail -5
    rssh "/tmp/harness-enable-ssh.sh '$DISK'"               | tail -3
    echo "ssh injection ok"
fi

echo "=== 2/5 booting the installed disk ==="
# Ship the boot script to the host: the harness usually lives on the workstation,
# not on the VM host, so referencing "$HERE" there would fail.
rssh_put /tmp/harness-vm-boot-installed.sh "$HERE/vm-boot-installed.sh" \
    || { echo "FAIL: could not copy boot script" >&2; exit 1; }
# setsid + nohup: the VM must outlive this ssh session, which exits immediately.
rssh "setsid nohup /tmp/harness-vm-boot-installed.sh --disk '$DISK' --ram $RAM --cpus $CPUS --ssh-port $SSH_PORT --web-port $WEB_PORT >/tmp/archy-vm-boot.log 2>&1 < /dev/null & echo started"
VM_STARTED=1

echo -n "waiting for guest ssh"
for _ in $(seq 1 60); do
    if rssh "timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/$SSH_PORT' 2>/dev/null"; then
        echo " — open"; break
    fi
    echo -n "."; sleep 3
done
rssh "timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/$WEB_PORT' 2>/dev/null" \
    || { echo; echo "FAIL: web port $WEB_PORT never opened on $SSH_HOST" >&2; exit 1; }

echo "=== 3/5 opening the tunnel ==="
# 127.0.0.1 on the host only; the guest is not published to the network.
ssh -N "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L "${LOCAL_WEB}:127.0.0.1:${WEB_PORT}" "$HOST" &
TUNNEL_PID=$!
for _ in $(seq 1 20); do
    curl -sf -o /dev/null "http://127.0.0.1:${LOCAL_WEB}/" && break
    sleep 1
done
curl -sf -o /dev/null "http://127.0.0.1:${LOCAL_WEB}/" \
    || { echo "FAIL: UI not reachable through the tunnel" >&2; exit 1; }
echo "tunnel ok: http://127.0.0.1:${LOCAL_WEB}/ -> $SSH_HOST:$WEB_PORT"

echo "=== 4/5 driving onboarding with Playwright ==="
OUT="${OUT_DIR:-$HERE/results/onboarding-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
ARCHY_UI_URL="http://127.0.0.1:${LOCAL_WEB}/" \
ARCHY_TEST_PASSWORD="$PASSWORD" \
OUT_DIR="$OUT" \
    node "$HERE/../tests/onboarding/onboard.cjs" || { echo "FAIL: onboarding driver failed" >&2; exit 1; }
echo "onboarding finished; artifacts in $OUT"

echo "=== 5/5 verifying the mesh in the guest ==="
# Pipe the assertion script in, so the guest never needs a copy of the harness.
if ssh "${GUEST_SSH_OPTS[@]}" -o "ProxyCommand=$PROXY" \
       -p "$SSH_PORT" root@127.0.0.1 "ARCHY_FIPS_VERSION='$EXPECT' bash -s" < "$HERE/verify-fips-onboarding.sh" \
       | tee "$OUT/verify.log"; then
    echo "RESULT: onboarding E2E PASS"
    exit 0
else
    echo "RESULT: onboarding E2E FAIL (see $OUT/verify.log)" >&2
    exit 1
fi
