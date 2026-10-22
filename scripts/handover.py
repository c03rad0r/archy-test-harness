#!/usr/bin/env python3
"""Generate the operator handover sheet from a run.

This is the second half of the harness's job: making the loop's output testable by a human
**in one sitting**.

The shape deliberately matches Archipelago's own acceptance idiom
(`docs/release-1.9.0-acceptance.md` in the SUT), which is the maintainers' established
format. Read from that file, its conventions are:

  * a dated `##` section per UAT event, appended over time;
  * the ngit proposal hash + merge commit + a statement about the mirror check;
  * an explicit sentence separating SOURCE-TEST results from OPERATOR UAT
    ("the isolated backend regression passed 1/1; ... an actual renewed payment remains
     operator UAT, not a source-test pass");
  * artifact identity (SHA256 per deployed binary/page, named per node) + rollback copies;
  * what was NOT covered, as "Open release gates" / "Retained regression scope" /
    "Explicit boundaries";
  * a closing sentence that refuses to over-claim.

THE RULE THIS ENFORCES: **no step may say "verify it works".** Every step carries a
command and an expected observation, so a human can decide pass/fail without judgement.
Steps are ordered read-only-first; anything destructive is last and gated.

Usage:
    ./scripts/handover.py --run 20261022T194500Z-fcc18b3
    ./scripts/handover.py --run latest-ish/id --out ~/handover/cdn.md
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS / "lib"))

from archy_harness.evidence import load_run  # noqa: E402
from archy_harness.markers import Outcome  # noqa: E402

# --------------------------------------------------------------------------------------
# The manual steps. Each is (title, command, expected observation, read_only).
#
# These are the things a human can check that the harness CANNOT assert for itself. They
# are written against the media path and the CDN deliverables; keep them honest — if a
# step cannot be done on a laptop, say so rather than inventing a simpler version.
# --------------------------------------------------------------------------------------

MANUAL_STEPS: list[dict] = [
    {
        "title": "The node starts and answers",
        "command": "curl -sS -m 5 http://127.0.0.1:5678/health",
        "expect": "HTTP 200 with a health payload. A connection refused means the binary "
                  "did not start; check the run's evidence/ for the node log.",
        "read_only": True,
    },
    {
        "title": "The media route exists and refuses an unauthenticated read",
        "command": "curl -sS -o /dev/null -w '%{http_code}\\n' -m 5 "
                   "http://127.0.0.1:5678/api/rental-playback/does-not-exist",
        "expect": "401 (unauthenticated) — NOT 404 and NOT 500. A 404 means the route is "
                  "not registered; a 500 means it reached a handler with a bad handle. "
                  "The SUT asserts exactly this at rental_playback.rs:507-547.",
        "read_only": True,
    },
    {
        "title": "A film longer than 15 minutes is not truncated (P3)",
        "command": "curl -sS -m 1000 -o /tmp/archy-long.bin -w 'bytes=%{size_download} "
                   "time=%{time_total}\\n' <the same URL a real player would use>",
        "expect": "size_download equals the full object size and time_total exceeds 900 s "
                  "for a film longer than 15 minutes at the observed rate. Before P3 this "
                  "download dies at exactly ~900 s with a truncated body. "
                  "THIS IS THE ONE STEP THAT CANNOT BE FAKED BY A UNIT TEST — the fix "
                  "lives in server.rs, which no in-process test exercises.",
        "read_only": True,
    },
    {
        "title": "Resume actually resumes (P4)",
        "command": "curl -sS -r 0-1023 -D - -o /dev/null <url>   # then\n"
                   "curl -sS -r 1024-2047 -D - -o /dev/null <url>",
        "expect": "Both answer 206 with a Content-Range whose start matches the request, "
                  "and the second response carries the same ETag as the first. If the "
                  "server answers 200 with the whole body, If-Range is not honoured.",
        "read_only": True,
    },
    {
        "title": "The meter moves on the MEDIA path, not the swarm path (D3)",
        "command": "curl -sS <the counter endpoint>   # before\n"
                   "curl -sS -o /dev/null <a media URL>   # stream one object\n"
                   "curl -sS <the counter endpoint>   # after",
        "expect": "The counter increases by the number of bytes served. **A passing test "
                  "in swarm/paid.rs proves nothing** — that layer gates iroh-blobs "
                  "(OTA-only, feature-off) and has never seen a video byte. If the "
                  "counter does not move here, D3 is implemented in the wrong file.",
        "read_only": True,
    },
    {
        "title": "Concurrent viewers do not 503 the node's own UI (P2)",
        "command": "for i in $(seq 1 20); do curl -sS -o /dev/null -w '%{http_code} ' "
                   "-m 10 <a media URL> & done; wait; echo\n"
                   "curl -sS -o /dev/null -w '%{http_code}\\n' <the UI URL>",
        "expect": "The UI still answers. Before P2 the only limit is one process-wide "
                  "Semaphore::new(1024) shared with the local UI (server.rs:1136), so at "
                  "exhaustion the node 503s everything including its own interface.",
        "read_only": False,
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(run: dict, run_root: Path, artifacts: list[Path]) -> str:
    sut = run.get("sut") or {}
    harness = run.get("harness") or {}
    counts = run.get("counts", {})
    results = run.get("results", [])
    today = datetime.date.today().isoformat()

    lines: list[str] = []
    add = lines.append

    add("# Archipelago CDN/streaming — operator handover")
    add("")
    add(f"**Generated:** {today} · **Run:** `{run.get('run_id')}`")
    add(f"**Harness:** `{harness.get('path')}` @ `{harness.get('commit', '')[:7]}`"
        + (" (dirty)" if harness.get("dirty") else ""))
    add("")

    # --- provenance -------------------------------------------------------------------
    add("## What was tested")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| SUT commit | `{sut.get('commit', 'unknown')}`"
        + (" **DIRTY TREE — results are not reproducible**" if sut.get("dirty") else "") + " |")
    add(f"| SUT describe | `{sut.get('describe', '')}` |")
    add(f"| SUT path | `{sut.get('path', '')}` |")
    add(f"| Venue | `{run.get('venue')}` |")
    add(f"| Profile | `{run.get('profile')}` |")
    add(f"| Started | {run.get('started_at')} |")
    add(f"| Finished | {run.get('finished_at')} |")
    add(f"| Host | `{(run.get('host') or {}).get('hostname')}` |")
    add("")

    # --- source tests vs operator UAT --------------------------------------------------
    add("## Source-test results (NOT operator acceptance)")
    add("")
    add("These are automated results. They are not a substitute for the manual steps below.")
    add("")
    add("| Outcome | Count |")
    add("|---|---|")
    for outcome in Outcome:
        add(f"| {outcome.value} | {counts.get(outcome.value, 0)} |")
    add("")
    if counts.get(Outcome.NOT_VERIFIED.value):
        add(f"> **{counts[Outcome.NOT_VERIFIED.value]} test(s) could not run.** "
            "`NOT_VERIFIED` is not a pass. If any of those is marked `required` it is "
            "counted as a FAIL above.")
        add("")
    if counts.get(Outcome.INCONCLUSIVE.value):
        add(f"> **{counts[Outcome.INCONCLUSIVE.value]} test(s) ran but the environment was "
            "not trustworthy** (interference detected). Re-run on a quiescent box.")
        add("")

    failed = [r for r in results if r["outcome"] in (Outcome.FAIL.value, Outcome.NOT_VERIFIED.value)]
    if failed:
        add("### Not passing — read before you start")
        add("")
        for result in failed[:40]:
            add(f"- `{result['outcome']}` **{result['name']}**")
            if result.get("reason"):
                add(f"  - {result['reason']}")
            if result.get("fix"):
                add(f"  - fix: `{result['fix']}`")
        if len(failed) > 40:
            add(f"- …and {len(failed) - 40} more; see `run.json`.")
        add("")

    # --- artifact identity ------------------------------------------------------------
    add("## Artifact identity")
    add("")
    if artifacts:
        add("SHA256 of everything handed over. Verify before trusting any result above.")
        add("")
        add("| Artifact | SHA256 | Bytes |")
        add("|---|---|---|")
        for artifact in artifacts:
            try:
                add(f"| `{artifact.name}` | `{sha256(artifact)[:32]}…` | {artifact.stat().st_size} |")
            except OSError:
                add(f"| `{artifact.name}` | unreadable | — |")
        add("")
    else:
        add("_No artifact paths given. If this handover ships a binary or a bundle, re-run "
            "with `--artifact <path>` so its hash is recorded. An acceptance document "
            "without artifact identity cannot be audited._")
        add("")

    # --- the manual steps -------------------------------------------------------------
    add("## Manual steps — do these in order")
    add("")
    add("Ordered read-only first. Each step has a command and the observation that decides "
        "pass/fail — no step says \"verify it works\".")
    add("")
    for index, step in enumerate(MANUAL_STEPS, start=1):
        gate = "read-only" if step["read_only"] else "**MUTATES STATE**"
        add(f"### {index}. {step['title']}  `[{gate}]`")
        add("")
        add("```bash")
        for command_line in step["command"].splitlines():
            add(command_line)
        add("```")
        add("")
        add(f"**Expect:** {step['expect']}")
        add("")
        add("- [ ] pass  - [ ] fail  - [ ] could not run (say why)")
        add("")

    # --- boundaries -------------------------------------------------------------------
    add("## Explicit boundaries — what this handover does NOT cover")
    add("")
    add("Stated so a reviewer is not misled by a green source-test run:")
    add("")
    add("- **The buyer playback proxy over FIPS is not verified here.** It needs a real FIPS "
        "path (`require_fips` → `is_service_active()` → DNS at `127.0.0.1:5354`); there is "
        "no injection point and no fallback is permitted (`dial.rs:683-689`). Any test "
        "claiming otherwise is claiming a mock.")
    add("- **The seller serve route is not verified here** without a real installed "
        "`indeedhub-api` app in `Running` state (`registration_pin.rs:45-68`).")
    add("- **Wire behaviour of `server.rs` is only exercised on the `localnode` venue.** "
        "An `inprocess` run bypasses the listener, the 1024-permit semaphore, the v6 "
        "mirror and the peer path filter — so P3's fix is NOT verified by an inprocess run.")
    add("- **Coverage is not measured in this repository.** There is no "
        "`.coverage-threshold`, no coverage step in any CI workflow, and no coverage tool "
        "in `CONTRIBUTING.md`. No coverage percentage is claimed anywhere in this document.")
    add("- **M1 (per-path vs per-node throughput) has not been run.** It is blocked on port "
        "5201 in the `inet fips` nft inbound allowlist on two receiver hosts.")
    add("")

    # --- the click ---------------------------------------------------------------------
    add("## What only the operator can do")
    add("")
    add("- **Merge.** The loop opens, updates and defends branches; it cannot land them.")
    add("- **Decide the upstream question.** Whether any of this is offered to `lfg2025` on "
        "ngit is a deliberate act, not a default.")
    add("- **Decide where finished work lands** — held as fork branches, or merged to "
        "`archy-fork/main`.")
    add("")
    add("## Closing statement")
    add("")
    add("This document records source-test results and the manual steps needed to accept "
        "them. It is **not** an upstream contribution, and it is **not** an acceptance of "
        "any deliverable by the `lfg2025` collective. Nothing here has been merged.")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="run id, or a path to run.json")
    parser.add_argument("--out", default=None, help="output path (default: alongside run.json)")
    parser.add_argument("--artifact", action="append", default=[],
                        help="a file to record the SHA256 of; repeatable")
    opts = parser.parse_args()

    try:
        run = load_run(opts.run)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run_root = Path(opts.run)
    if not run_root.is_file():
        base = Path(__import__("os").environ.get("ARCHY_RESULTS_DIR", "results")).expanduser()
        run_root = base / opts.run
    else:
        run_root = run_root.parent

    text = render(run, run_root, [Path(a).expanduser() for a in opts.artifact])
    out = Path(opts.out).expanduser() if opts.out else run_root / "operator-handover.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}  ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
