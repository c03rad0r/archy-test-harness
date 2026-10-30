#!/usr/bin/env bash
# Run from a client connected to the paid SSID (archipelago-tollgate-*).
#
# A green node UI or a router-local :2121 response is NOT evidence that a
# paid client is gated. This script checks the client's actual view:
#   - the captive portal is reachable;
#   - the TollGate API is reachable;
#   - normal internet is blocked before payment.
#
# Usage: scripts/paid-client-smoke.sh [portal-ip]
# Default portal-ip: 192.168.99.1 (br-tollgate)
set -euo pipefail

PORTAL_IP=${1:-192.168.99.1}
PORTAL_URL="http://${PORTAL_IP}:2050/"
API_URL="http://${PORTAL_IP}:2121/"
INTERNET_URL=${INTERNET_URL:-https://example.com/}
FAIL=0

say() { printf '%s\n' "$*"; }
check_ok() { # label, command...
    local label=$1; shift
    if "$@" >/dev/null 2>&1; then
        say "PASS: $label"
    else
        say "FAIL: $label"
        FAIL=1
    fi
}
check_blocked() { # label, command...
    local label=$1; shift
    if "$@" >/dev/null 2>&1; then
        say "FAIL: $label (unauthenticated client reached the internet)"
        FAIL=1
    else
        say "PASS: $label (blocked as expected)"
    fi
}

say "Paid-client smoke against ${PORTAL_IP}"
check_ok "portal HTTP 200/3xx" curl -fsS -L -m 8 -o /dev/null "$PORTAL_URL"
check_ok "TollGate API reachable" curl -fsS -m 8 -o /dev/null "$API_URL"
# An unauthenticated client may get a portal redirect rather than a timeout.
# curl -f only accepts 2xx, so a 3xx/portal response is a failure here.
check_blocked "internet blocked before payment" curl -fsS -m 8 -o /dev/null "$INTERNET_URL"

if [ "$FAIL" -ne 0 ]; then
    say "RESULT: FAIL — do not treat the node's green UI as a paying-customer proof."
    exit 1
fi
say "RESULT: PASS — client-side portal/API reachable and internet gated."
