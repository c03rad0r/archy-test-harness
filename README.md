# archy-test-harness

End-to-end test harness for **Archipelago** (`lfg2025/archy`) — a bootable Bitcoin-node
OS with a Rust backend at `core/`, FIPS mesh transport, and Cashu ecash.

This repo exists to answer one question the upstream repo cannot answer today:

> **Does a film actually play, end to end, and can I prove it to a human?**

The architecture is deliberately borrowed from
[`OpenTollGate/physical-router-test-automation`](https://github.com/OpenTollGate/physical-router-test-automation)
(PRTA), which solves the same class of problem for OpenWrt routers: a test suite that must
run against several venues, produce evidence a release gate can consume, and hand results
to an operator for manual acceptance. What was borrowed, and what was deliberately *not*
borrowed, is recorded in [`docs/PRTA-WHAT-WE-BORROWED.md`](docs/PRTA-WHAT-WE-BORROWED.md).

**The SUT lives elsewhere.** This repo never vendors Archipelago. It is pointed at a
checkout with `ARCHY_SUT`.

---

## Quick start

```bash
# 1. Point at an Archipelago checkout
export ARCHY_SUT=~/repos/archy

# 2. See what would run, without running anything
./scripts/run-profile.py --profile smoke --list

# 3. Run the smoke profile (no hardware, no podman, no FIPS)
./scripts/run-profile.py --profile smoke

# 4. Read the result
./scripts/report.py --run latest
```

---

## Fleet automation (IaC)

The make targets are thin aliases over the scripts and the canonical
`run-profile.py`. `HOST=` runs a target on a Hermes fleet host; an empty `HOST`
runs locally (T14 is a control point, not a workload host).

```bash
make host-check HOST=x240
make build HOST=x240 ARCHY_REF=fix/media-stream-deadline
make smoke HOST=x240
make media-local HOST=x240
make iso HOST=x240 REF=fix/media-stream-deadline BUILD_ONLY=1
make vm-install HOST=t440 ARCHY_ISO=/path/on/t440.iso
make usb ARCHY_ISO=/path/to.iso STICK=/dev/sdc
make media-soak NODE=x280 URL=https://node/content/... MINUTES=16
```

See [`docs/PLAN.md`](docs/PLAN.md), [`docs/MAKE-TARGETS.md`](docs/MAKE-TARGETS.md),
[`docs/HOSTS.md`](docs/HOSTS.md), [`docs/PR-SPLIT.md`](docs/PR-SPLIT.md) and
[`docs/PR-READINESS.md`](docs/PR-READINESS.md).

## Venues

A **venue** is "where the node under test runs". Tests never know which venue they are on;
the venue exports a connection contract as environment variables.

| Venue | What it is | Needs | Status |
|---|---|---|---|
| `inprocess` | `ApiHandler` constructed directly on a tempdir; no listener | nothing | implemented |
| `localnode` | The real `archipelago` binary on loopback, private data dir | a built binary | implemented (see limits) |
| `twonode` | Two processes joined over FIPS | FIPS daemons on both ends | **not implemented — blocked** |
| `hardware` | A real installed Archipelago box | rootless podman + apps | **not implemented — blocked** |
| `iso` | A **built installer image**, read as an artefact | `ARCHY_ISO` + `xorriso` | implemented |

`inprocess` and `localnode` are the workhorses. `twonode` and `hardware` are declared so
the taxonomy is complete and so a test can declare what it needs — but they raise
`VenueUnavailable` rather than pretending. See
[`docs/VENUES.md`](docs/VENUES.md) and [`docs/BLOCKERS.md`](docs/BLOCKERS.md).

`iso` is the odd one: nothing runs on it. The image is the thing under test, because two
defects have already shipped past a green suite — a daemon version bumped in source but
absent from the image, and a systemd unit masked in a static test but not in the artefact.
It reads the shipped root filesystem and its own dpkg database, so it asserts what the
image *contains*, not what the build log claimed. Booting is a separate concern: see
[`docs/INSTALLER-CONSTRAINTS.md`](docs/INSTALLER-CONSTRAINTS.md) and
`scripts/vm-boot-installer.sh`.

**Liveness is a TCP connect, never ICMP.** Learned from PRTA the hard way.

---

## The `required` rule

A test marked `@pytest.mark.required` that cannot run (its venue is unavailable, a
dependency is missing) is reported as **FAILED — NOT VERIFIED**, never as a skip.

This is the single most important rule in the harness. Without it, a green run means "we
didn't crash", and a release gate cannot consume it. With it, a green run means "everything
we claimed to verify, we verified". A skip is only allowed when the test is *not* marked
required — and the report names every skip explicitly.

---

## Result artifacts

Every run writes `results/<run_id>/`:

```
results/20261022T194500Z-fcc18b3/
├── run.json          # the machine-readable record: SUT commit, venue, versions, outcomes
├── report.md         # human-readable, generated
├── junit.xml         # pytest JUnit, for CI consumption
├── evidence/         # per-test evidence: response transcripts, headers, counter deltas
└── operator-handover.md   # the manual-testing sheet, generated (see below)
```

`run.json` carries the full provenance tuple — SUT commit, dirty-tree flag, venue, harness
commit, python/binary versions, and a four-outcome vocabulary
(`PASS` / `FAIL` / `NOT_VERIFIED` / `INCONCLUSIVE`). **A result that cannot name its own
provenance is not evidence.**

No "newest run directory" shorthand: every command takes an explicit `--run <id>`, because
`ls -dt` is silently wrong the moment two runs overlap.

---

## The operator handover

The harness's second job is to make the loop's output testable by a human in one sitting.

```bash
./scripts/handover.py --run latest --out ~/handover/archy-cdn-2026-10-22.md
```

This generates a sheet that matches Archipelago's own acceptance idiom
(`docs/release-*-acceptance.md` in the SUT): a dated section, source-test results separated
from operator UAT, artifact identity, **numbered copy-pasteable steps each with an expected
observation**, ordered read-only-first, and an explicit *boundaries* section listing what
this handover does **not** cover.

The rule it enforces: **no step may say "verify it works".** Every step has a command and
an observable result.

---

## Commands

| Command | Purpose |
|---|---|
| `./scripts/run-profile.py --profile <p>` | Run a named profile (see `profiles/`) |
| `./scripts/run-profile.py --profile <p> --list` | Show selection without running |
| `./scripts/report.py --run <id>` | Render a run's report |
| `./scripts/handover.py --run <id>` | Generate the operator handover sheet |
| `./scripts/venue.py --probe` | Report which venues are available on this box, and why not |
| `./scripts/doctor.py` | Check host prerequisites (cargo, binary, podman, fips0) |

There is exactly **one** canonical entry point (`run-profile.py`). `make` targets are thin
aliases that call it; they never contain logic of their own.

---

## Layout

```
lib/archy_harness/
  venue.py        # the venue contract (4 verbs) + the declared venues
  env.py          # the env-var connection contract
  markers.py      # marker taxonomy + the `required` gate
  evidence.py     # run artifacts, provenance, four-outcome vocabulary
  sut.py          # locating, building and identifying the SUT
  proc.py         # process fixtures (spawn the real binary, bounded)
  http.py         # a thin client for the hand-written dispatcher
tests/
  unit/           # pure logic; no node, no network
  inprocess/      # ApiHandler in-process (fast, broad)
  live/           # the real binary on loopback
  media/          # the media path (the reason this repo exists)
  iso/            # the built installer image: contents, wiring, installer constraints
profiles/         # YAML: name -> marker selection + venue + budget
scripts/          # the canonical entry points
docs/             # venues, blockers, PRTA provenance, writing-a-test
results/          # gitignored; one dir per run
```

---

## Status — read this before trusting a green run

This harness is **new and partially blocked**. What actually works today, and what raises
`VenueUnavailable` instead, is maintained in [`docs/BLOCKERS.md`](docs/BLOCKERS.md). Do not
read a green run as full coverage until that file says the media blocker is closed.

## Licence

MIT. The SUT (Archipelago) is MIT and belongs to the `lfg2025` collective — this repo is a
fork-side harness, not an upstream claim.
