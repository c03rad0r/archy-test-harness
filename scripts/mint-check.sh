#!/usr/bin/env bash
# Check the test mint over IPv4 and (optionally) its IPv6/FIPS front.
#
# Usage:
#   make mint-check
#   MINT_HOST=192.168.50.166 MINT_FIPS=fd5d:... scripts/mint-check.sh
set -euo pipefail

HOST=${MINT_HOST:-192.168.50.199}
PORT=${MINT_PORT:-3338}
FIPS=${MINT_FIPS:-}
SSH_TARGET=${MINT_SSH:-archipelago@${HOST}}

check_http() {
  local base=$1
  curl -fsS --max-time 5 "${base}/v1/info" >/dev/null
  curl -fsS --max-time 5 "${base}/v1/keys" >/dev/null
  echo "ok ${base}"
}

check_http "http://${HOST}:${PORT}"

if [ -n "${FIPS}" ]; then
  if curl -g -fsS --max-time 5 "http://[${FIPS}]:${PORT}/v1/info" >/dev/null 2>&1; then
    check_http "http://[${FIPS}]:${PORT}"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=8 "${SSH_TARGET}" \
      "curl -g -fsS --max-time 5 'http://[${FIPS}]:${PORT}/v1/info' >/dev/null &&
       curl -g -fsS --max-time 5 'http://[${FIPS}]:${PORT}/v1/keys' >/dev/null"
    echo "ok http://[${FIPS}]:${PORT} (via ssh)"
  fi
fi
