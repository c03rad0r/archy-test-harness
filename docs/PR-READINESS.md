# PR readiness

A PR is ready when the evidence matches the claim, not when the tests are
green.

## Rules

1. **One feature per PR.** No drive-by edits, no progress/report files.
2. **A test that can fail.** Show the failing case before the fix.
3. **Real binary or VM evidence.** Source assertions alone are not enough for
   listener, systemd or image behaviour.
4. **Name the boundary.** State what the PR does not prove.
5. **No fork-only dependencies.** The branch must apply to upstream main.
6. **Upstream CI and the sanctioned runner.** They are different paths; run
   both.

## Evidence by feature

| Feature | Required evidence |
|---|---|
| P3 | unit test, media route reachable on the real binary, >15 min stream on x280 |
| P4 | ETag, If-Range, resume and stale-range refusal on the wire |
| P2 | UI responsive while concurrent media streams run |
| P1 | counter delta and `monitoring.media` RPC on the real binary |
| D3 | byte cap refusal mid-stream with correct accounting |
| D1 | before/after benchmark of catalogue open cost |
| FIPS | existing VM onboarding video and ISO package/mask tests |

## Evidence bundle

Every run writes `results/<run_id>/`:

```text
run.json          provenance and outcomes
report.md         human report
junit.xml         CI consumption
evidence/         transcripts, headers, counter deltas
operator-handover.md
```

The PR body should link the run id, the SUT commit, the harness commit, the
venue and the operator steps that remain.

## Boundaries to state explicitly

- `inprocess` does not exercise `server.rs` (listener, semaphore, path filter).
- `localnode` cannot produce a real purchased media body.
- `twonode` requires a real FIPS pair.
- `hardware` requires a real installed IndeeHub app.
- The 900 s duration is wall-clock UAT, not a unit test.
