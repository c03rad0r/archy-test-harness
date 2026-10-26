# Venues

A **venue** is where the node under test runs. Tests never know which venue they are on —
they declare what they need with a `venue_*` marker, and the venue exports a connection
contract as environment variables.

Borrowed from PRTA, where the whole contract is four verbs and a venue's only job is to
export env vars the rest of the suite already reads (`lib/venue.py:219-338`,
`lib/labgrid_venue.py:162-192`). That makes venue support additive: adding one requires zero
test changes.

## The contract

| Verb | Where | Purpose |
|---|---|---|
| `probe(name) -> (ok, reason, fix)` | `venue.py` | Can this venue run here? Never guesses. |
| `require(name) -> VenueSpec` | `venue.py` | Same, but raises `VenueUnavailable` carrying the reason. |
| `VenueConfig.to_env()` | `venue.py` | The **only** authoritative spelling of the connection contract. |
| `apply(cfg, respect_existing=)` | `venue.py` | Export it. `respect_existing=False` clears a stale target. |

The env contract:

```
ARCHY_VENUE      which venue is active
ARCHY_BASE_URL   the RPC/HTTP surface
ARCHY_DATA_DIR   the node's data directory
ARCHY_BINARY     path to the binary, if the venue runs one
ARCHY_PEER_ADDR  the peer listener address, if any
ARCHY_FIPS_DNS   the FIPS resolver address, if any
ARCHY_SCRATCH    scratch space the venue owns
```

`apply(..., respect_existing=False)` **drops a stale `ARCHY_BASE_URL` first**. A leftover
value from a previous target is how a run silently tests the wrong box — PRTA learned this
(`lib/labgrid_venue.py:182-188`).

## Liveness is a TCP connect, never ICMP

`venue.tcp_open()`. On the TollGate bench the router drops ICMP (100% loss) while every
service answers — so `ping` is not a liveness test. The same is true of any host with a
firewall that REJECTs or DROPs ICMP while serving TCP.

## The four venues

### `inprocess` — available now

`ApiHandler` constructed directly on a tempdir; no listener, no wire. The pattern is proven in
the SUT at `rental_playback.rs:507-547`.

**Can assert:** deny paths, auth, CORS, health, static surfaces; RPC methods that only touch
the data dir; the rental-window per-chunk logic with an injected clock.

**Cannot assert:** anything about the listener — the 1024-permit semaphore, the v6 mirror,
the peer path filter. **In particular, P3's fix in `server.rs` is NOT verifiable here.**

### `localnode` — available once a binary is built

The real `archipelago` binary on loopback with a private data dir.

**Can assert:** wire behaviour of `server.rs` — the real listener, the real semaphore, the
real path filter, the 900 s deadline.

**Cannot assert:** the buyer playback proxy (needs a real FIPS dial), the seller serve route
(needs an installed IndeeHub app).

**Known limitation:** `Config::load()` reads only `/etc/archipelago/config.toml`
(`config.rs:114`), and `ARCHIPELAGO_CONFIG` is ignored. `proc.start_node()` therefore cannot
point the binary at a private config without writing a system path, which a test must never
do. Closing this needs either a SUT-side config-override or a container. See
`docs/BLOCKERS.md` B3.

### `twonode` — BLOCKED

Two processes joined over a real FIPS mesh. Needs `fips0` up **and** the FIPS DNS answering
at `127.0.0.1:5354`. Blocked by B1: there is no transport injection point and no fallback is
permitted (`dial.rs:683-689`).

### `hardware` — BLOCKED

A real installed Archipelago box (rootless podman + apps). Blocked by B2: the seller serve
route requires a real `Running` `indeedhub-api` app.

### `iso` — available now

A **built installer image**, read as an artefact. This is the only venue where nothing runs:
the ISO is the thing under test. Set `ARCHY_ISO` to a built image; `xorriso` must be
installed.

It exists because two defects shipped past a green suite — a daemon version bumped in
source but absent from the image, and a systemd unit masked by a static test on the builder
but not inside the artefact that would have been flashed. Reading the image's own root
filesystem and dpkg database closes that gap.

**Can assert:** which packages the image ships and at what version (per its own
`var/lib/dpkg/status`), which systemd units are masked or enabled, that the image has both
El Torito BIOS and UEFI boot entries, that the image matches its `.sha256` sidecar.

**Cannot assert:** any runtime behaviour. Nothing executes here. In particular it says
nothing about whether the daemon *starts* — on a fresh install it deliberately does not,
because `archipelago-fips.service` is gated on the key that onboarding creates. See
[`INSTALLER-CONSTRAINTS.md`](INSTALLER-CONSTRAINTS.md).

Booting the image is deliberately **not** a pytest lane: it needs KVM, a SATA scratch disk
larger than 30 GiB, and three interactive keypresses. That is
`scripts/vm-boot-installer.sh`.

## Adding a venue

1. Add a `VenueSpec` to `VENUES` in `lib/archy_harness/venue.py`, with honest
   `can_assert` / `cannot_assert` entries and a probe.
2. Add its marker to `VENUE_MARKERS` in `lib/archy_harness/markers.py`.
3. Add it to `pytest.ini`'s marker list.
4. If it needs a new env var, add it to `VenueConfig` and `to_env()` — nowhere else.

`tests/unit/test_venue_and_evidence.py` asserts every venue declares both what it can and
what it cannot assert, so an incomplete venue fails the smoke run.

## Why there is no lock file yet

PRTA has three overlapping lock families with a documented "hold two in a fixed order"
invariant, and its own extract calls that a symptom rather than a design. This harness has
**no shared physical resource** yet, so it has **no lock**. When one exists, the pattern to
copy is PRTA's `bench_lock` (one flock, one holder line) — not its other two.
