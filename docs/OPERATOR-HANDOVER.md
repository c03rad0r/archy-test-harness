# Operator handover — what the ralph loop must produce

This document defines the **completion contract** for a ralph/goal loop driving the
Archipelago CDN deliverables. It answers the question the current plan answers with one
line: *"write the handover"* — with no definition of what a handover is.

The rule: **when the loop has driven the deliverables as far as it can, a human must be able
to accept or reject the work in one sitting, without reading code.**

---

## 1. What the loop produces

Three artifacts, in this order. None is optional.

### 1.1 A run, not a claim

```bash
./scripts/run-profile.py --profile critical --venue localnode
./scripts/report.py --run <id>
```

`results/<run_id>/run.json` is the machine-readable record: SUT commit, dirty flag, venue,
harness commit, and one outcome per test from a four-value vocabulary. **A result that cannot
name its own provenance is not evidence.**

A run whose SUT tree is dirty is flagged loudly in the report and in the handover — because
it is not reproducible from the commit alone.

### 1.2 A handover sheet, generated not written

```bash
./scripts/handover.py --run <id> --artifact <binary-or-bundle> --out ~/handover/archy-cdn.md
```

The generator exists so the sheet cannot drift from the run it describes. Its shape matches
Archipelago's own acceptance idiom (`docs/release-1.9.0-acceptance.md` in the SUT):

- a dated section;
- **source-test results explicitly separated from operator UAT**;
- artifact identity (SHA256) for everything handed over;
- numbered manual steps, each with a command and an expected observation;
- an explicit **boundaries** section;
- a closing statement that refuses to over-claim.

### 1.3 A boundaries section that is honest

The single most useful part. It must name, at minimum:

- which venue each claim was verified on, and what that venue **cannot** show
  (an `inprocess` run does not exercise `server.rs` — so P3 is not verified by it);
- every test that is `NOT_VERIFIED`, with its reason;
- the merge click and the upstream decision, as operator-only actions;
- the fact that **no coverage percentage is claimed**, because the SUT measures none.

---

## 2. The rule that makes a green run mean something

A test marked `required` that cannot run is **FAILED — NOT VERIFIED**, never skipped.

So the loop's exit code is meaningful: `0` means everything it claimed to verify, it
verified. `1` means something is unverified or failed. `2` means something ran but the
environment was not trustworthy (`INCONCLUSIVE` — e.g. a service bounced mid-run).

**The loop may not "fix" a FAIL by removing the `required` marker.** Removing it is a claim
that the check is no longer release-relevant, and it must be stated in the boundaries section
with a reason.

---

## 3. What the loop must NOT do

- **Do not claim a pass from a unit test for a wire-behaviour change.** P3 lives in
  `server.rs`. No in-process test can verify it. Say `UNVERIFIED` and name the venue needed.
- **Do not substitute a mock for a real dependency.** A mock proves the client code path, not
  conformance. Report `UNVERIFIED` and why.
- **Do not rebuild what the SUT already has.** `process_fixture.rs` already proves the
  purchase protocol across a process restart with a fake mint; `tests/lifecycle/` already has
  a bats release gate. Extend them.
- **Do not report a coverage number.** There is no coverage tooling in the SUT. A number
  nobody measured is worse than no number.
- **Do not treat CI-green as equivalent** to the mandated runner's green. They are different
  paths (see `docs/BLOCKERS.md` B6).
- **Do not open upstream PRs unprompted.** Upstream work lives on ngit and is a deliberate
  operator act.

---

## 4. The manual steps a human can actually do

`scripts/handover.py` ships these, each with an expected observation. They are ordered
read-only-first:

| # | Step | Decides |
|---|---|---|
| 1 | The node starts and answers | the binary runs at all |
| 2 | The media route refuses an unauthenticated read (401, not 404/500) | the route exists and fails closed |
| 3 | **A film longer than 15 minutes is not truncated** | P3 — and no unit test can fake this |
| 4 | Resume actually resumes (206 + matching Content-Range + stable ETag) | P4 |
| 5 | **The meter moves on the media path, not the swarm path** | D3 — a pass in `swarm/paid.rs` proves nothing |
| 6 | Concurrent viewers do not 503 the node's own UI | P2 |

Step 3 and step 5 are the two that catch the failures this programme exists to prevent:
a truncated film, and a meter wired into a file that never sees a video byte.

---

## 5. What only the operator can do

- **Merge.** The loop opens, updates and defends branches; it cannot land them.
- **Decide the upstream question** — whether any of this is offered to `lfg2025` on ngit.
- **Decide where finished work lands** — held as fork branches, or merged to
  `archy-fork/main`. *This is currently unspecified in the CDN plan and should be settled
  before the loop starts, because it changes the loop's operator-channel section.*

---

## 6. Definition of done for the loop

All of the following, observed in tool output, not asserted:

1. `./scripts/run-profile.py --profile critical` exits with every `required` test verified on
   its venue, or with the unverifiable ones named in the boundaries section.
2. `results/<run_id>/run.json` exists and names the SUT commit.
3. `operator-handover.md` is generated and its boundaries section lists every
   `NOT_VERIFIED`.
4. Every branch is pushed and its tip verified with `git ls-remote`.
5. The merge queue is stated as a list, so the operator knows what to click.

Then: **stop.** Do not keep running to look busy. A human-gated boundary is a correct
terminal state — name the exact click and stop.
