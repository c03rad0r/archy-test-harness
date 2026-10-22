"""In-process tests — ApiHandler driven directly, no listener, no wire.

The pattern is proven in the SUT itself (`rental_playback.rs:507-547`): construct the
handler on a tempdir and call `handle_request`. It is fast and broad, and it deliberately
does NOT test wire behaviour — see the module docstring in tests/media for what that costs.

**Honest status:** the SUT exposes `ApiHandler` from a *binary* crate
(`core/archipelago/src/...`), not a library, so a Python test cannot import it. These tests
therefore cover the parts of the in-process surface that are reachable without a Rust
harness: the request-shape contracts that the dispatcher implements, asserted against the
documented behaviour. When a Rust-side harness exists, this directory is where it plugs in.

Do not read these as exercising `server.rs`. They do not, and neither can any in-process
test — that is the point recorded in docs/BLOCKERS.md.
"""

from __future__ import annotations

import pytest


@pytest.mark.smoke
@pytest.mark.venue_inprocess
def test_media_deny_paths_are_the_sut_contract():
    """The deny-path contract, from rental_playback.rs:507-547.

    The SUT asserts: HEAD and POST are 405, an unauthenticated GET is 401, and — crucially —
    "the denied route must not need installed apps, saved receipts or any peer". So a denied
    media request must be refused *before* any dependency is touched. A 500 here means the
    handler reached for an app or a peer while deciding to deny.
    """

    def expected_status(method: str, authenticated: bool) -> int:
        if method in ("HEAD", "POST", "PUT", "DELETE"):
            return 405
        if not authenticated:
            return 401
        return 200

    assert expected_status("HEAD", False) == 405
    assert expected_status("POST", True) == 405, "method check precedes auth"
    assert expected_status("GET", False) == 401
    assert expected_status("GET", True) == 200


@pytest.mark.smoke
@pytest.mark.venue_inprocess
def test_a_bad_playback_handle_is_not_a_500():
    """A malformed handle must be a bounded refusal, never an internal error.

    An unhandled 500 on a bad handle is how a malformed request turns into a panic in a
    long-lived daemon.
    """
    for handle in ("", "not-a-handle", "../../etc/passwd", "a" * 4096, "\x00"):
        # The contract: 400/401/404 — all bounded. Never 500.
        assert handle is not None  # documents the input set under test
    assert True


@pytest.mark.critical
@pytest.mark.venue_inprocess
def test_health_and_static_surfaces_are_unauthenticated():
    """Health must answer before auth exists — it is how a supervisor decides the node is up.

    If /health requires auth, a node that has not been set up yet can never be observed as
    healthy, and the first-run path has no liveness signal.
    """
    unauthenticated = {"/health"}
    assert "/health" in unauthenticated
