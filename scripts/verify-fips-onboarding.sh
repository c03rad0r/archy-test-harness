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

# The backend creates the identity key and starts the wrapper asynchronously after
# the wizard's "Set up Node" click. Verifying immediately therefore races it: the
# first fresh-install run reported "identity key missing / not active" on a node
# that was healthy moments later (observed 2026-10-26 — same disk, same script,
# PASS with 2 peers once the daemon had come up).
#
# So wait, bounded, for the state that is supposed to appear. This is not a retry
# of a failing assertion: nothing is asserted until the window expires.
WAIT="${ARCHY_ONBOARD_WAIT:-120}"
echo "=== waiting up to ${WAIT}s for onboarding to take effect ==="
waited=0
while [ "$waited" -lt "$WAIT" ]; do
    if [ -f /var/lib/archipelago/identity/fips_key ] \
       && [ "$(systemctl is-active archipelago-fips 2>/dev/null)" = "active" ]; then
        echo "ready after ${waited}s"
        break
    fi
    sleep 5
    waited=$((waited + 5))
done
[ "$waited" -lt "$WAIT" ] || echo "window expired after ${waited}s — asserting what is there"

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
# Mesh convergence is slower than daemon start: the node must dial rendezvous and
# authenticate. Wait bounded for a *connected* peer before treating its absence as
# a failure — otherwise a healthy node on a slow network reads as broken.
peer_waited=0
while [ "$peer_waited" -lt "$WAIT" ]; do
    if fipsctl show peers 2>/dev/null | grep -q '"connectivity": *"connected"'; then
        break
    fi
    sleep 5
    peer_waited=$((peer_waited + 5))
done
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
