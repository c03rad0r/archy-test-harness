# Upstream PR split

No upstream PRs are filed in this phase. This document records the intended
split so testing can be organized around it.

## Two tracks

- **PR branch**: cut from upstream `fcc18b3`; only the feature and its tests.
- **Integration branch**: fork `main` (FIPS + VM work) plus selected features.
  Install this on x280/t440. Never install a raw PR branch.

## Order

| Order | Feature | Commits | Why |
|---:|---|---|---|
| 1 | P3 900 s media deadline | `d046d02` | smallest, clear bug, tested |
| 2 | P4 ETag / If-Range resume | `c15709c`, `8f5e717`, `d076d83` | user-visible correctness |
| 3 | P2 peer/UI budgets | `badb8e4`, `058025f`, `a99b5a3` | after P3; same `server.rs` |
| 4 | P1 counters, then RPC | 9 commits | split into two PRs |
| 5 | D3 byte cap | 6 commits | depends on P1 |
| — | FIPS 0.5.3 | fork main PR #4 | separate, VM evidence exists |
| — | D1 snapshot index | `9d50f73` | defer until benchmark |
| — | D4a disk_limit | `bf5207c` etc. | do not upstream as code |

## Exclude from PRs

- `PROGRESS.md`, `REPORT.md`, hand-off docs.
- The VM harness commits (they belong here, not in the SUT).
- Drive-by refactors and unrelated formatting.

## Readiness checklist

- [ ] Branch based on upstream `fcc18b3`
- [ ] Only feature + tests + a short design note
- [ ] `cargo fmt --all -- --check`
- [ ] `cargo test` and the sanctioned isolated runner
- [ ] Harness `smoke`, `critical`, `media`, `media-local`
- [ ] ISO build + VM install/onboarding when image-affecting
- [ ] Evidence linked in the PR body
- [ ] Boundaries/known gaps stated
- [ ] ngit PR only; no duplicate Gitea PR
