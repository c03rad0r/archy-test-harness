# Plan: Archipelago upstream PRs + fleet test infrastructure

Status: harness IaC + test-x280 runbook implemented; USB write and binary staging pending.
Updated: 2026-10-27.

This is the version-controlled plan. The operator checklist is at the end.

## Objective

Turn the fork's experimental work into a small number of well-scoped upstream
PRs that are likely to merge on the first attempt, backed by real-machine
evidence. Keep fork-only or low-value work out of upstream. Make the test loop
repeatable infrastructure with make targets, merged to the default branch.

Constraints:

- No build/test workload on T14. It may be an SSH/Ansible control point only.
- Workloads run on Hermes fleet machines (x280, x240, t440) and the shared
  build host `inr2` once access works.
- The x280 is the primary physical node and currently has no OS.
- No upstream PRs are filed until the operator explicitly asks.

## Current state

```text
upstream lfg2025/archy main        fcc18b3
fork main                          8b6d8c  (FIPS 0.5.3 + VM harness work)
fork fix/media-stream-deadline     9d50f7  (33 commits, based on fcc18b3)
harness main                       c8c3035
```

The feature branch is based on `fcc18b3`, not fork `main`. It does not contain
the FIPS/VM work. Build an **integration branch** (fork main + selected
features) for installation; keep **PR branches** based on upstream `fcc18b3`.

Existing VM evidence:

- `docs/evidence/onboarding-fips-v0.5.3.mp4`
- `docs/ONBOARDING-E2E.md`: measured on KVM on t440, fips 0.5.3-1
- fork main commits `a3e804f2`, `91fafa1a`, `d97deaac`, `72b37754`

That evidence supports the FIPS 0.5.3 PR, not P3/P4/P1.

## Upstream PR strategy

Upstream only when all hold:

1. Correctness/security fix or clearly user-visible.
2. One feature, one concern, minimal diff.
3. A test fails without it.
4. Real-binary/VM evidence, not only source assertions.
5. Fits upstream design; design-heavy work gets an ADR/issue first.
6. No dependency on fork-only commits.

Do not upstream: progress/report docs, D4a code (ext4 cannot enforce it), D1
without a benchmark, the VM harness (different repo), or combinations.

| Feature | Commits | Value | Upstream? |
|---|---|---|---|
| P3 900 s media deadline | `d046d02` | high | first |
| P4 ETag / If-Range resume | `c15709c`, `8f5e717`, `d076d83` | high | second |
| P2 peer/UI budgets | `badb8e4`, `058025f`, `a99b5a3` | medium-high | third |
| P1 observability | 9 commits | medium | split counters/RPC |
| D3 byte cap | 6 commits | high security | after P1 |
| D1 snapshot index | `9d50f73` | performance | defer |
| D4a disk_limit | `bf5207c` etc. | XFS-only | issue/docs, no code |
| FIPS 0.5.3 | fork main PR #4 | high | separate |
| VM harness | fork main | harness | move here |
| PROGRESS/REPORT | many | fork evidence | exclude |

Evidence per PR:

| PR | Must show |
|---|---|
| P3 | unit, wire reachability, >15 min real stream on x280 |
| P4 | ETag/If-Range/resume wire tests |
| P2 | UI responsive under concurrent media load |
| P1 | counter deltas on the real binary and RPC |
| D3 | byte-cap refusal mid-stream |
| D1 | before/after benchmark |
| FIPS | existing VM video and ISO tests |

Contribution platform: ngit is canonical; Gitea is a mirror. One focused ngit
PR per feature. No duplicate Gitea PRs. No PRs are filed in this phase.

## Test architecture

### Host roles

| Host | Cores | RAM | Free | KVM | Role |
|---|---:|---:|---:|---|---|
| x280 | — | 8 GB | 1 TB | — | primary node / UAT |
| x240 | 4 | 7.5 GB | 57 GB | no | build host, then second node |
| t440 | 4 | 7.5 GB | 78 GB | yes | VM host when load is low |
| inr2 | 16–20 | large | 50→250 GB | ? | shared build/VM host (blocked) |
| T14 | 18 | 30 GB | 547 GB | yes | control only |

`inr2` access as of 2026-10-27: port 22023 accepts TCP but no SSH banner; port
22 answers and rejects all local keys. Key, port or persistence is wrong. The
public key fingerprint expected by the operator is
`SHA256:1Nci9JaBmkMu8FuyBQmmq5wiS/DjOhKftUWNikRJkOE`.

### Make targets

See [`MAKE-TARGETS.md`](MAKE-TARGETS.md). The test-x280 physical runbook is
[`MANUAL-X280.md`](MANUAL-X280.md) and has a checklist. `HOST=` runs over SSH via
`scripts/remote.sh`; an empty `HOST` runs locally. T14 is not a valid workload
host.

### Build-host gap

`scripts/build-host-check.sh` requires 8 cores, 7 GB RAM, 40 GB free, KVM and
podman. No current Hermes host passes fully. Use the build-only mode
(`host_role=build`) and limit `CARGO_BUILD_JOBS` on x240; use `inr2` once
access and the disk upgrade are ready.

## Install x280 in parallel

Yes, once the **integration build** is VM-validated:

- x280 is idle and blank; reinstall is ~12 minutes.
- It unblocks the 15-minute P3 UAT and hardware validation.
- Keep the official ISO as fallback.
- x240 becomes the second node for the FIPS buyer-proxy test.

## Checklist

### Access and hosts
- [ ] Fix `inr2` SSH port/key/persistence with the friend
- [ ] Verify `ssh debian@inr2... 'nproc; free -h; df -h /'`
- [ ] Add/update `inr2` in `inventory/hosts.yml`
- [ ] Decide build host: x240 now, inr2 later
- [ ] Decide VM host: t440 when load < 4
- [ ] No workloads on T14

### Harness IaC
- [x] Makefile with build/test/ISO/VM/USB/node targets
- [x] Ansible inventory and host role
- [x] `build-sut.sh`, `remote.sh`
- [x] `vm-install-auto.sh`
- [x] `usb-iso.sh`
- [x] `node-check.sh`
- [x] `media-soak.sh`
- [x] `pr-check.sh`
- [x] Plan/host/target/PR docs
- [x] `make lint` green
- [x] Pushed to `c03rad0r/archy-test-harness` main (canonical repo is read-only)

### Build and test
- [x] `make host-check HOST=x240` green
- [x] `make host-bootstrap HOST=x240` (sudo)
- [x] `make build HOST=x240` (debug, `fix/media-stream-deadline`)
- [x] `make smoke HOST=x240` (44 PASS)
- [x] `make media-local HOST=x240` (6 PASS, twonode NOT VERIFIED)
- [ ] Build ISO on x240: running; fixed esptool, podman registry, isolinux path, Rust 1.99, pkg-config/OpenSSL, apps context
- [ ] `make vm-install HOST=t440`
- [ ] `make onboarding HOST=t440`
- [ ] Install x280 from the same ISO
- [ ] P3: >15 min seller stream on x280
- [ ] x280 + x240 FIPS pair for buyer proxy
- [ ] Evidence bundle per PR

### Upstream (deferred; no PRs yet)
- [ ] P3 PR branch off upstream `fcc18b3`
- [ ] P4, P2, P1, D3 in order
- [ ] FIPS PR separately
- [ ] D1/D4a remain fork-only
