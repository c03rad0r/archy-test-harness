#!/usr/bin/env bash
# Assert that onboarding actually brought the FIPS mesh up.
#
# Run INSIDE the guest (the e2e runner pipes it over ssh). Exit code is the
# verdict: 0 only if every check passes. Each check prints one `PASS:`/`FAIL:`
# line so a failure is attributable without reading the whole log.
#
# Why these checks and not "the dashboard loaded": the dashboard renders whether
# or not the mesh works. The daemon is gated on an identity key that onboarding
# creates, so "page loads" and "mesh is up" are genuinely different facts.
set -uo pipefail

EXPECT_VERSION="${ARCHY_FIPS_VERSION:-0.5.3}"
FAILED=0

check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "PASS: $name"; else echo "FAIL: $name"; FAILED=1; fi
}

echo "=== identity materialised by onboarding ==="
if [ -f /var/lib/archipelago/identity/fips_key ]; then
    echo "PASS: identity key exists ($(stat -c '%s bytes, mode %a' /var/lib/archipelago/identity/fips_key))"
    [ -f /var/lib/archipelago/identity/fips_key.pub ] \
        && echo "PASS: public key exists" || { echo "FAIL: no fips_key.pub"; FAILED=1; }
else
    echo "FAIL: /var/lib/archipelago/identity/fips_key missing — onboarding did not run to completion"
    FAILED=1
fi

echo "=== supervisor wiring ==="
# Exactly one supervisor: the package unit must be masked and the wrapper enabled.
target=$(readlink -f /etc/systemd/system/fips.service 2>/dev/null || true)
if [ "$target" = "/dev/null" ]; then
    echo "PASS: packaged fips.service is masked"
else
    echo "FAIL: packaged fips.service is not masked (target=${target:-none})"; FAILED=1
fi

[ "$(systemctl is-enabled archipelago-fips 2>/dev/null)" = "enabled" ] \
    && echo "PASS: archipelago-fips enabled" || { echo "FAIL: archipelago-fips not enabled"; FAILED=1; }
[ "$(systemctl is-active archipelago-fips 2>/dev/null)" = "active" ] \
    && echo "PASS: archipelago-fips active" || { echo "FAIL: archipelago-fips not active"; FAILED=1; }

echo "=== daemon ==="
got=$(fips --version 2>/dev/null | head -1)
echo "version: ${got:-<none>}"
case "$got" in
    "fips ${EXPECT_VERSION} "*|"fips ${EXPECT_VERSION}") echo "PASS: daemon version $EXPECT_VERSION" ;;
    *) echo "FAIL: expected fips ${EXPECT_VERSION}, got '${got:-<none>}'"; FAILED=1 ;;
esac

echo "=== mesh ==="
peers_json=$(fipsctl show peers 2>/dev/null || true)
if [ -z "$peers_json" ]; then
    echo "FAIL: fipsctl show peers returned nothing (control socket absent?)"; FAILED=1
else
    connected=$(printf '%s' "$peers_json" | grep -c '"connectivity": *"connected"' || true)
    echo "connected peers: $connected"
    if [ "${connected:-0}" -ge 1 ]; then
        echo "PASS: at least one authenticated peer"
        printf '%s' "$peers_json" | grep -E '"display_name":|"connectivity":' | head -8
    else
        echo "FAIL: no connected peers"; FAILED=1
    fi
fi

echo "=== verdict ==="
if [ "$FAILED" -eq 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL"; fi
exit "$FAILED"
