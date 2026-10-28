#!/usr/bin/env bash
# Deploy the fork's backend binary to an installed Archipelago node, or restore
# the stock binary.
#
# The first run backs up /usr/local/bin/archipelago to .stock; --restore puts it
# back. The service is restarted and its status/log tail are printed.
#
# Usage: scripts/deploy-fork-binary.sh --node user@host --binary PATH
#        scripts/deploy-fork-binary.sh --node user@host --restore
set -euo pipefail

NODE=""
BINARY=""
RESTORE=0
SERVICE=${SERVICE:-archipelago}

while [ $# -gt 0 ]; do
  case "$1" in
    --node) NODE=${2:?}; shift 2 ;;
    --binary) BINARY=${2:?}; shift 2 ;;
    --restore) RESTORE=1; shift ;;
    --service) SERVICE=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$NODE" ] || { echo "FAIL: --node is required" >&2; exit 2; }
command -v ssh >/dev/null || { echo "FAIL: ssh not installed" >&2; exit 1; }

if [ "$RESTORE" = 1 ]; then
  echo "restoring the stock binary on $NODE"
  ssh -t "$NODE" "SERVICE='$SERVICE' bash -s" <<'REMOTE'
set -euo pipefail
[ -f /usr/local/bin/archipelago.stock ] || { echo "FAIL: no .stock backup"; exit 1; }
sudo install -m 0755 /usr/local/bin/archipelago.stock /usr/local/bin/archipelago
sudo systemctl restart "$SERVICE"
sleep 2
systemctl --no-pager status "$SERVICE" || true
REMOTE
  exit 0
fi

[ -n "$BINARY" ] || { echo "FAIL: --binary is required" >&2; exit 2; }
[ -f "$BINARY" ] || { echo "FAIL: binary not found: $BINARY" >&2; exit 1; }

LOCAL_SHA=$(sha256sum "$BINARY" | awk '{print $1}')
echo "binary: $BINARY ($LOCAL_SHA)"
echo "node:   $NODE"

scp "$BINARY" "$NODE:/tmp/archipelago.new"
REMOTE_SHA=$(ssh "$NODE" "sha256sum /tmp/archipelago.new" | awk '{print $1}')
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] || {
  echo "FAIL: upload hash mismatch ($LOCAL_SHA != $REMOTE_SHA)" >&2
  exit 1
}

ssh -t "$NODE" "SERVICE='$SERVICE' bash -s" <<'REMOTE'
set -euo pipefail
BIN=/usr/local/bin/archipelago
if [ ! -f "$BIN.stock" ]; then
  sudo cp -a "$BIN" "$BIN.stock"
  echo "backed up stock binary to $BIN.stock"
fi
sudo install -m 0755 /tmp/archipelago.new "$BIN"
sudo systemctl restart "$SERVICE"
sleep 2
systemctl --no-pager status "$SERVICE" || true
echo "--- recent journal ---"
sudo journalctl -u "$SERVICE" -n 100 --no-pager || true
REMOTE
