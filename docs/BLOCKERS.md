# Blockers — what this harness honestly does NOT cover

Read this before trusting a green run. Every entry names the exact code that causes it, so
it can be re-checked rather than believed.

The harness's rule is: **a blocker is reported, never papered over.** A test that cannot run
is `NOT_VERIFIED`; if it is marked `required` it is `FAIL`. No mock is ever substituted for a
real dependency and called a pass.

---

## B1 — The buyer playback proxy needs a real FIPS path (blocks `twonode`)

**Status: OPEN.** Blocks the single most important end-to-end assertion — "a buyer streams
bytes from a seller".

`/api/rental-playback/{handle}` requires a working FIPS mesh:

- `require_fips` → `is_service_active()`
- peer address resolution via DNS at `127.0.0.1:5354` (`fips/dial.rs:89`)
- the peer listener bound to the `fips0` ULA on `PEER_PORT = 5679` (`dial.rs:36`)
- `fips0_ula()` read from `/proc/net/if_inet6` for interface `fips0`
  (`fips/iface.rs:26-36`)

**There is no injection point, and no fallback is permitted** (`dial.rs:683-689`). The SUT
even asserts the failure mode itself (`dial.rs:1072-1081`).

**What would close it:** either two hosts with a real FIPS daemon each, or a refactor that
introduces a transport seam. The latter is a production change and is not this harness's
call to make. Until then, `venue_twonode` tests report `NOT_VERIFIED`.

**Detection:** `./scripts/venue.py --probe` → `twonode` unavailable with the exact reason.

---

## B2 — The seller serve route needs a real installed IndeeHub app (blocks `hardware`)

**Status: OPEN.** This is the biggest blocker to single-box media testing.

`registration_pin::installed_context` requires
`state.package_data["indeedhub"].state == Running && installed.is_some()`
(`registration_pin.rs:45-68`); `APP_ID = "indeedhub-api"` (`registered_media.rs:24`).

It is called from the seller serve route (`registered_media.rs:216-230`), the rental control
route (`:227-230`), the playback proxy (`rental_playback.rs:227-230`) and all three
`media.registration.*` RPCs (`media_registration.rs:154/175/217`).

A real install means rootless podman + the signed catalog + a container runtime.

**The cheapest unlock, untested:** `installed_context` takes `&DataModel`
(`registration_pin.rs:45-49`) and `StateManager` is injectable (`mod.rs:107-118`), so a
fixture `DataModel` carrying a fabricated `Running indeedhub` record might satisfy it without
a container. **This is INFERRED, not verified.** It is the first thing a harness author
should try — and if it works, B2 becomes a code change in this repo rather than a hardware
requirement.

---

## B3 — No HTTP-level harness for the daemon

**Status: MOSTLY RESOLVED (corrected 2026-10-23). The old text here was wrong, and the
way it was wrong matters.**

The previous claim was that there was no way to start the real `Server` with a fixture
config, so the `localnode` venue could not run it and P3 was unverifiable. That was
measured again and is **false**:

- **`ARCHIPELAGO_DATA_DIR` IS honoured** — `config.rs:125`
  (`if let Ok(data_dir) = std::env::var("ARCHIPELAGO_DATA_DIR")`). Related overrides also
  honoured: `ARCHIPELAGO_BIND` (`:129`), `ARCHIPELAGO_LOG_LEVEL` (`:139`),
  `ARCHIPELAGO_CONTAINER_RUNTIME` (`:151`), `ARCHIPELAGO_PORT_OFFSET` (`:155`).
- `ARCHIPELAGO_CONFIG` is genuinely ignored (`Config::load()` reads only
  `/etc/archipelago/config.toml`, `config.rs:114`) — that part stands.
- `ARCHIPELAGO_DEV_MODE` at `config.rs:147-149` is **not** "deliberately ignored"; it is
  read and applied. The `:165` entry is `ARCHIPELAGO_DEV_DATA_DIR`.

Observed: the real release binary starts, binds, and serves with only env overrides —

    ARCHIPELAGO_DATA_DIR=<tmp> ARCHIPELAGO_BIND=127.0.0.1:<port> ARCHIPELAGO_DEV_MODE=1 \
        core/target/release/archipelago
    → "Data directory: <tmp>", writes <tmp>/archipelago.pid
    → GET /health → HTTP 200
    → GET /content/... → HTTP 404 (route reachable and answering, not refused)

**How the wrong claim survived:** the first read-back attempt did not set the override,
got `Failed to write PID marker / Permission denied` against the hardcoded
`/var/lib/archipelago`, and that result was written up as an environment blocker. The
blocker was the caller. A claimed blocker is an assertion too — it needs the same
observed command as any other claim.

**What still genuinely blocks startup:** a container runtime. Without podman/docker the
node stops at `Error: Failed to create auto runtime / Caused by: Neither Podman nor
Docker is available`. Install podman (Ubuntu 24.04 candidate 4.9.3; verified working).
Note the isolation implication: check whether the host is a real archy node before
installing app dependencies — `/var/lib/archipelago` absent and 0 archy systemd units
means it is not.

**What is STILL not achievable, and this is the real residue of B3:** the layer above the
listener is reachable now, but there is no way to *fixture a purchase*, so an actual
media **body** cannot be produced. Wire-level reachability is provable; media
**duration** (P3's literal criterion, >900 s) still needs a real purchased stream. Report
the distinction rather than collapsing it.

Venue status after the correction: `inprocess`, `localnode` and `hardware` available;
`twonode` missing (FIPS DNS 127.0.0.1:5354 not answering).

---

## B4 — Coverage is not measured anywhere in the SUT

**Status: STRUCTURAL — not fixable from here.** This blocker is about *absence*, so it cites
the files that were checked and found empty rather than a line:

| Where coverage would be configured | What is there |
|---|---|
| `$ARCHY_SUT/.coverage-threshold` | does not exist |
| `$ARCHY_SUT/CONTRIBUTING.md` | no `coverage`, `tarpaulin` or `llvm-cov` |
| `$ARCHY_SUT/AGENTS.md` | no coverage requirement |
| `.github/workflows/ci.yml` | no coverage step (verified at `ci.yml:14-56`) |
| `.github/workflows/build-macos.yml` | no coverage step |
| `.gitea/workflows/post-install-tests.yml` | no coverage step |

The fleet-wide `AGENTS.md` requires "coverage ≥ the repo's `.coverage-threshold` (default
80%)", so **on this repo that gate silently defaults and is unenforceable**.

**This harness claims no coverage percentage anywhere, on purpose.** A number nobody measured
is worse than no number, because it gets quoted. The substitute rule is narrower and
checkable: *every new public function has at least one test that fails without it.*

---

## B5 — The SUT's own lint gate fails on a pristine checkout

**Status: OPEN — and it blocks the CDN plan's acceptance criterion 1.**

`CONTRIBUTING.md:24` requires `cargo clippy --all-targets --all-features -- -D warnings` to
pass. On a clean `fcc18b3` it does not:

```
$ cd $ARCHY_SUT/core
$ cargo fmt --all -- --check                              # exit 0
$ cargo clippy -p archipelago --bin archipelago -- -D warnings
error: could not compile `archipelago` (bin "archipelago") due to 123 previous errors
EXIT=101
```

**123 pre-existing errors** (124 warning lines un-suppressed). Any lint gate here must be a
**ratchet** — "must not increase" — not an absolute. `lib/archy_harness/sut.py:clippy_baseline()`
measures it; the CDN plan needs the same fix.

---

## B6 — CI and the sanctioned test runner are different paths

**Status: KNOWN DIVERGENCE.**

The SUT mandates `scripts/test-backend-isolated.sh` (unrestricted `cargo test` reaches real
services on an installed node). But `.github/workflows/ci.yml:56` runs bare
`cargo test --all-features` on `ubuntu-latest`. **CI's green is not the sanctioned runner's
green**, and the isolation guarantee does not hold in CI.

The runner also needs `sudo -n true` plus `systemd-run`/`unshare`/`setpriv`, or
`ARCHY_TEST_IMAGE` + `ARCHY_TEST_CARGO_HOME` for its podman backend
(`test-backend-isolated.sh:17-18, 75-78`). It accepts exactly three packages (`:8-13`) and
asserts exactly one test executable (`:95`) — so a test in `tests/*.rs` cannot run through it
at all.

---

## B7 — M1 (per-path vs per-node throughput) has never been run

**Status: BLOCKED ON A FIREWALL RULE.**

M1 decides whether replication multiplies capacity (`k·P`) or merely rearranges it. It is
blocked on adding port 5201 (`iperf3`) to the `inet fips` nft inbound allowlist on two
receiver hosts. UFW already allows all of `fips0`, so `ufw allow` is the wrong lever — the
deny is in a separate nft table.

Anchors for re-checking: the peer transport this measures is bound at `fips/dial.rs:36`
(`PEER_PORT = 5679`) and resolved at `fips/dial.rs:89` (`FIPS_DNS_ADDR`); the interface the
rule applies to is read at `fips/iface.rs:26-36` (`fips0_ula()`).

Any rule added must be **removed afterwards and the removal reported**. This harness does not
touch firewall rules.

---

## Not a blocker, but worth knowing

- **`server.rs:1460-1494` is a second path allowlist.** `is_peer_allowed_path` must contain
  any new media route or it 404s before reaching a handler. Its own tests are at
  `server.rs:2414-2443`.
- **Long timeouts are baked in**: a 24 h inner `PeerRequest` timeout, a 20 s outer one, and
  900 s on several routes (`content_server.rs:675`, `api/rpc/content.rs:909,1108`,
  `handler/proxy.rs:260`, `bootstrap.rs:156`). The clock is injectable only *below* the HTTP
  layer, so a test that trips one of these hangs for minutes.
- **One global `Semaphore::new(1024)`** is shared by every listener (`server.rs:1136`), so a
  concurrency test contends with app-port relay traffic — the drain logic can become the
  thing under test.
