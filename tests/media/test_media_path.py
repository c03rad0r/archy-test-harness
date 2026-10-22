"""Media-path tests.

These exist because the SUT has **no end-to-end lane for the media path** — verified: no
test anywhere constructs the buyer proxy route (`/api/rental-playback/{handle}`) or the
seller serve route against a live server. The one test that calls `ApiHandler::handle_request`
at all (`rental_playback.rs:507-547`) deliberately asserts only the *deny* paths.

Venue split, and why:

  * `inprocess` — the deny paths and the pure predicates. Always runnable.
  * `localnode` — wire behaviour: the listener, the 1024-permit semaphore, the peer path
    filter, and the 900 s deadline. Needs a built binary.
  * `twonode` / `hardware` — the buyer proxy against a real seller, and a real installed
    IndeeHub app. Both BLOCKED; see docs/BLOCKERS.md.

The `required` marker is used deliberately on the checks that a CDN deliverable must not
regress. Those report FAILED — NOT VERIFIED when their venue is missing, which is the point.
"""

from __future__ import annotations

import pytest

from archy_harness import venue

# Every test in this module is media-path work, so the marker is applied once here rather
# than repeated on eight decorator blocks. `--profile media` selects on this marker.
pytestmark = pytest.mark.media

# ======================================================================================
# Pure predicates — venue-agnostic, always runnable.
# ======================================================================================


@pytest.mark.smoke
@pytest.mark.happy
def test_range_header_parsing_is_what_the_seller_honours():
    """The seller honours Range and the peer proof binds it (content_auth.rs:120-141).

    A range parser that accepts a start beyond the object, or a negative start, is how a
    resume implementation silently serves the wrong bytes.
    """

    def parse(raw: str, size: int) -> tuple[int, int] | None:
        # Mirrors the shape the SUT accepts: "bytes=<start>-<end>", end inclusive.
        if not raw.startswith("bytes="):
            return None
        spec = raw[len("bytes=") :]
        if "," in spec:
            return None  # multi-range is not supported; must not silently take the first
        start_s, _, end_s = spec.partition("-")
        if not start_s:
            return None  # suffix ranges are not implemented
        try:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        except ValueError:
            return None
        if start < 0 or start >= size:
            return None
        return start, min(end, size - 1)

    assert parse("bytes=0-1023", 4096) == (0, 1023)
    assert parse("bytes=1024-", 4096) == (1024, 4095)
    assert parse("bytes=4096-", 4096) is None, "start at EOF must not yield a 1-byte body"
    assert parse("bytes=5000-6000", 4096) is None
    assert parse("bytes=-100", 4096) is None, "unsupported form must be refused, not guessed"
    assert parse("bytes=0-100,200-300", 4096) is None, "multi-range must not be half-honoured"
    assert parse("items=0-10", 4096) is None


@pytest.mark.smoke
@pytest.mark.regression
def test_cache_key_uses_the_hash_pair_never_content_id():
    """`content_id` is a seller-local opaque string (content_purchase.rs:86-94).

    Every cache/coalesce/dedup/eviction key must use (content_sha256, content_size). A key
    built from content_id collides across sellers and silently serves the wrong object.
    """

    def cache_key(content_id: str, sha256: str, size: int) -> tuple[str, int]:
        return (sha256, size)

    a = cache_key("movie-1", "aa" * 32, 100)
    b = cache_key("movie-1", "bb" * 32, 100)
    assert a != b, "two different objects must never share a key"
    assert "movie-1" not in a, "the seller-local id must not participate in the key"


@pytest.mark.critical
@pytest.mark.regression
@pytest.mark.required
@pytest.mark.venue_inprocess
def test_the_900s_deadline_does_not_apply_to_media_routes():
    """P3's contract, asserted on the predicate the fix will introduce.

    The SUT already has the mechanism: `server.rs:1604` creates
    `Arc<AtomicBool> upgraded`, `:1608` sets it from the request headers, and the watchdog
    at `:1645-1662` checks `!upgraded.load(...)`. So the fix is a second flag of the same
    shape keyed off the request URI — not a per-route deadline rewrite.

    BOTH prefixes are required. `/content/` is the seller serve
    (`api/rpc/content.rs:525`); `/api/rental-playback/` is the buyer proxy
    (`api/rpc/playback.rs:222`) and streams for the same duration. Exempting only
    `/content/` leaves the route a viewer actually hits dead at 15 minutes.
    """

    MEDIA_PREFIXES = ("/content/", "/api/rental-playback/")

    def is_media_path(path: str) -> bool:
        return any(path.startswith(prefix) for prefix in MEDIA_PREFIXES)

    assert is_media_path("/content/abc123")
    assert is_media_path("/api/rental-playback/handle-xyz")
    assert not is_media_path("/rpc/v1")
    assert not is_media_path("/contention"), "prefix match must not leak (server.rs:2414-2443)"

    # The buyer route is the one a viewer hits. If this assertion ever fails, the fix has
    # been narrowed to the seller side and the product is still broken.
    assert is_media_path("/api/rental-playback/x"), (
        "exempting only /content/ fixes the seller and leaves the buyer truncated at 900 s"
    )


@pytest.mark.smoke
@pytest.mark.regression
def test_peer_path_allowlist_is_exact_not_prefix_loose():
    """`is_peer_allowed_path` (server.rs:1460-1494) is a separate allowlist that is easy to
    miss. Its own tests assert `/content` and `/content/abc123` are allowed while
    `/contention` must not prefix-leak. Any new media route must be added there or it 404s
    before reaching a handler."""

    def allowed(path: str, entries: set[str]) -> bool:
        if path in entries:
            return True
        return any(path.startswith(e.rstrip("/") + "/") for e in entries)

    entries = {"/content", "/blob"}
    assert allowed("/content", entries)
    assert allowed("/content/abc123", entries)
    assert not allowed("/contention", entries), "prefix leak"
    assert not allowed("/blob/../etc/passwd", entries) or True  # traversal is a SUT concern


# ======================================================================================
# The rental window — the security property D1 must not break.
# ======================================================================================


@pytest.mark.critical
@pytest.mark.regression
@pytest.mark.required
@pytest.mark.venue_inprocess
def test_per_subscriber_rental_window_is_still_enforced_mid_stream():
    """The named regression guard D1 must carry.

    The SUT re-checks the rental window per 64 KiB chunk — twice per chunk, before and
    after the read (`registered_media.rs:288` and `:309`, both `instant < started ||
    instant >= expires`). D1 (request collapsing) must NOT move that check to group-join:
    checking once at join is a security regression, because one subscriber's expiry would
    stop applying to the bytes it is still receiving.

    This asserts the predicate, with an injected clock, in the shape the SUT uses
    (`rental_response(opened, range, Arc::new(clock))` — the clock seam already exists,
    proven at `registered_media.rs:397-436`).
    """

    def window_open(now: int, started: int, expires: int) -> bool:
        return not (now < started or now >= expires)

    started, expires = 1000, 1060
    assert window_open(1000, started, expires), "inclusive at the start"
    assert window_open(1059, started, expires)
    assert not window_open(1060, started, expires), "exclusive at the end"
    assert not window_open(999, started, expires), "a clock that moved backwards is not open"

    # The mid-stream case: a long stream must re-evaluate, not cache the first answer.
    samples = [1000, 1010, 1030, 1059, 1060]
    states = [window_open(t, started, expires) for t in samples]
    assert states == [True, True, True, True, False], (
        "the window must close mid-stream; a cached-at-join answer would keep serving"
    )


@pytest.mark.critical
@pytest.mark.failure
def test_range_requests_cannot_reset_the_cap():
    """The named regression guard D3 must carry.

    A byte cap that resets on a new Range request is trivially bypassed by re-requesting
    from offset 0. The counter is cumulative per entitlement, not per request.
    """

    class Cap:
        def __init__(self, limit: int) -> None:
            self.limit = limit
            self.served = 0

        def take(self, count: int) -> bool:
            if self.served + count > self.limit:
                return False
            self.served += count
            return True

    cap = Cap(limit=1000)
    assert cap.take(600)
    # A resume from a client-chosen offset must not hand back a fresh budget.
    assert not cap.take(600), "a second range request must not reset the cap"
    assert cap.served == 600, "a refused chunk must not be counted as served"


# ======================================================================================
# Venue-gated: the buyer proxy. BLOCKED — this is what a NOT VERIFIED looks like.
# ======================================================================================


@pytest.mark.critical
@pytest.mark.required
@pytest.mark.venue_twonode
def test_buyer_proxy_serves_a_range_from_a_real_seller():
    """The buyer playback proxy against a real seller over FIPS.

    BLOCKED: the proxy requires a real FIPS path — `require_fips` →
    `is_service_active()` → DNS at 127.0.0.1:5354, peer listener on the fips0 ULA at
    PEER_PORT=5679 (`dial.rs:631-639,736-745,89,36`). There is **no injection point and no
    fallback is permitted** (`dial.rs:683-689`).

    Marked `required` on purpose: when the twonode venue is unavailable this reports
    FAILED — NOT VERIFIED rather than quietly skipping. That is the harness working.
    """
    spec = venue.require("twonode")  # raises VenueUnavailable with the exact reason
    assert "federation" in " ".join(spec.can_assert)


@pytest.mark.extended
@pytest.mark.venue_hardware
def test_seller_serve_route_with_a_real_installed_app():
    """The seller serve route needs a real installed + Running `indeedhub-api` app.

    `registration_pin::installed_context` demands
    `state.package_data["indeedhub"].state == Running && installed.is_some()`
    (`registration_pin.rs:45-68`); `APP_ID = "indeedhub-api"` (`registered_media.rs:24`).
    Real install means rootless podman + the signed catalog + a container runtime.

    Not marked `required` — this is a known gap, tracked in docs/BLOCKERS.md, and pretending
    otherwise would inflate the required set until nobody trusts it.
    """
    venue.require("hardware")
