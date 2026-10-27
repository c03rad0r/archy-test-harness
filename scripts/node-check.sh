#!/usr/bin/env bash
# Read-only health check for an installed Archipelago node.
#
# Usage: scripts/node-check.sh user@host
set -euo pipefail

NODE=${1:-${NODE:-}}
[ -n "$NODE" ] || { echo "usage: node-check.sh user@host" >&2; exit 2; }

ssh -o BatchMode=yes -o ConnectTimeout=10 "$NODE" 'bash -s' <<'REMOTE'
set -u
echo "== identity =="
hostname
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
uname -r
uptime

echo "== failed units =="
systemctl --no-pager --failed 2>/dev/null || true

echo "== arch services =="
systemctl list-units --type=service --no-pager 2>/dev/null | grep -i arch || true

echo "== listeners =="
ss -ltnp 2>/dev/null | grep -E ':(80|443|5959)\b' || true

echo "== http =="
for port in 80 5959; do
  code=$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" 2>/dev/null || true)
  printf '  port %-4s /health -> %s\n' "$port" "${code:-no-response}"
done

echo "== disk =="
df -h / | tail -1
REMOTE
