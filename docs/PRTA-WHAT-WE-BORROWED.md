# What we borrowed from PRTA, and what we deliberately did not

The architecture comes from
[`OpenTollGate/physical-router-test-automation`](https://github.com/OpenTollGate/physical-router-test-automation)
(PRTA), which solves the same class of problem for OpenWrt routers: one suite, several
venues, evidence a release gate can consume, and a human acceptance step.

Two documents back this:

- `~/reports/archy-test-harness/PRTA-ARCHITECTURE-EXTRACT.md` (1046 lines, 174 file:line
  citations) — what PRTA actually does.
- `~/reports/archy-test-harness/ARCHY-TEST-SURFACE.md` (799 lines) — what Archipelago
  actually offers to test.

**PRTA's own Prime Directive is "never create a parallel kit — build on the existing one."**
That was Felix's instruction for TollGate router work. Archipelago is a *different product*
and a codebase we are a guest in, so a separate harness is correct here — but the directive's
*spirit* still applies: we inherit PRTA's proven architecture instead of inventing one, and we
do not rebuild anything the SUT already has.

---

## Borrowed — the five patterns worth carrying

### 1. The venue seam: an env-var contract + four verbs

`lib/archy_harness/venue.py`. PRTA's whole venue contract is four functions
(`lib/venue.py:219-338`), and a venue hands the suite **environment variables**, not objects
(`lib/labgrid_venue.py:162-192`). That makes venue support *additive*: adding a venue requires
zero test changes.

Kept: the env-var contract, one `VenueConfig`, capability probes that return
`(ok, reason, fix)`.

### 2. `required`: a skip becomes FAILED — NOT VERIFIED

`lib/archy_harness/markers.py`. This is the single most important idea in PRTA
(`pytest.ini:28`, `conftest.py:1402-1413`). Without it, a green run means "we didn't crash".
With it, a green run means "everything we claimed to verify, we verified".

Kept exactly, including the narrow documented exemption (venue-specific tests stay skippable
by design).

### 3. A canonical run artifact with its own provenance

`lib/archy_harness/evidence.py`. `results/<run_id>/run.json` carries the full tuple — SUT
commit, dirty flag, venue, harness commit, versions — and a four-outcome vocabulary.
"Evidence that names its own provenance is the only evidence a release gate can consume."

Kept, with two corrections PRTA documented against itself (below).

### 4. Fail-closed capability probes with an exit-code API

`venue.probe()` returns `(ok, reason, fix)`; `venue.require()` raises `VenueUnavailable`
carrying the same. PRTA's device-identity script is the model
(`scripts/bench/device-identity.sh`): source-bound, fail-closed, exit code as API.

Kept in spirit. We have no physical-device pinning yet — when a `hardware` venue is real, this
is the pattern to copy.

### 5. A registry driving a thin façade, with one canonical entry point

`scripts/run-profile.py` is the only runner; `profiles/*.yaml` is the registry; the `make`
targets (when added) are thin aliases with no logic.

Kept as the *shape*. PRTA ended up with four live entry layers (Make, scripts, a pymake
registry, profiles) and its own extract calls that a mistake.

---

## Deliberately NOT copied — PRTA's own documented mistakes

Each of these is called out in PRTA's extract §10 as something that caused trouble there.

| PRTA mistake | What we do instead |
|---|---|
| **Three overlapping lock families** (`hardware.lock` 1 h, `routers.lock` 2 h, `bench-mt3000.lock` flock), with a documented "hold two in a fixed order" invariant | Not built yet. When exclusivity is needed: **one** flock primitive with a holder line, one file per exclusive resource. We have no shared physical device today, so there is nothing to lock — inventing the lock before the resource would repeat the mistake. |
| **A 1568-line root `conftest.py`** with interleaved hooks whose ordering is load-bearing and comment-only | Root `conftest.py` here is 20 lines. All logic is in `archy_harness.pytest_plugin`, registered explicitly via `pytest_plugins`. |
| **Two run-directory naming schemes** (`test-<ts>` vs `<ts>-<sha>`) | One scheme, and it includes the SUT commit: `<UTC stamp>-<short sha>`. |
| **"Operate on the newest run dir"** (`ls -dt results/*/ \| head -1`) | Every command takes an explicit `--run <id>`. `load_run()` refuses to guess and says why in the error. |
| **A 70 KB Makefile with 252 targets**, where the tier×client×backend matrix explodes | One runner + YAML profiles. Markers are a filter, paths are the selector. |
| **Four live entry layers** | One: `scripts/run-profile.py`. |
| **`pytest.ini` marker list as the taxonomy's only home**, with real semantics 1400 lines away | Each marker's gate lives in `markers.py` beside its declaration; one hook consumes it. |
| **A venue enum that is only a defaults axis** (`Venue.BAY` vs `Venue.ISLAND`) | `VenueConfig` is a config record. Venues differ by capability, not by topology name. |
| **Two co-existing venue modules** (`lib/venue.py` + `lib/labgrid_venue.py`) | Exactly one: `venue.py`. |
| **`MockRouter` subclassing the real `Router`** (the mock must track every method, and drifts silently) | Capability probes + `VenueUnavailable`. No mock subclass of a production class. |
| **`time.sleep` as synchronisation**, patched away in mock mode | `Node.wait_ready()` polls a TCP predicate with a deadline. Never a fixed sleep. |
| **~35 markers, several vestigial or near-synonymous** (`hardware` / `physical_hardware` / `physical_only`; `cloud_lab_only` with zero users) | 14 markers, four families, every one with a consumer. Asserted by a test: no duplicate names across families, every marker has a description. |

---

## Corrections PRTA documented about itself, applied here

1. **`--expected-pr` coupling** — the suite's meaning depended on a CLI flag. We do not
   couple test selection to a PR number. Provenance is recorded in the run artifact instead.
2. **Soft dependency on the publish path** (`nak` installed at runtime, warning if it fails)
   — "publish silently did nothing". We have no publish path yet; when added, a missing
   publisher must fail the run, not warn.
3. **`required` adoption is "deliberate curation"** with no list of which tests should carry
   it. We curate explicitly: `required` goes on checks a CDN deliverable must not regress, and
   the count is kept small enough that a FAIL is always meaningful.

---

## What the SUT already had, that we do NOT rebuild

This is the most important section. Archipelago has a real test estate; the harness's job is
to *join it*, not replace it.

| SUT asset | Why we leave it alone |
|---|---|
| `scripts/test-backend-isolated.sh` | The mandated unit runner. We do not run unit tests; the SUT does, better. |
| `wallet/payment_tests/process_fixture.rs` (533 lines) | Spawns real processes, runs a real loopback seller, proves lost-reply recovery across a restart without double payment. **The highest-value asset for the CDN work.** We must not rebuild it. |
| `tests/lifecycle/` + `run-gate.sh` | 22 bats suites, a 5× release gate, destructive tiers gated behind env vars. Our harness plugs in here. |
| `tests/multinode/smoke.sh` | The only assertion-style multi-node suite (federation pairing, peer browse, tombstone). |
| `scripts/run-e2e-tests.sh` | ~50 RPC methods. Its Content section covers only `content.list-mine` — **no media coverage**, which is the gap we fill. |
| `tests/regression/*` | ~20 targeted probes. |
| `docs/release-*-acceptance.md` | The maintainers' human-acceptance idiom. Our `handover.py` matches its shape rather than inventing one. |

**Rule for contributors:** if you are about to write a test that duplicates any row above,
stop. Extend it instead.
