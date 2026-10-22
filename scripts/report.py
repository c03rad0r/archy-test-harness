#!/usr/bin/env python3
"""Render a run's report from its run.json.

    ./scripts/report.py --run 20261022T175243Z-fcc18b3
    ./scripts/report.py --run results/20261022T175243Z-fcc18b3/run.json
    ./scripts/report.py --run <id> --json

There is no "latest" shorthand on purpose: `ls -dt results/*/ | head -1` is silently wrong
the moment two runs overlap (a mistake PRTA documented against itself).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS / "lib"))

from archy_harness.evidence import load_run, summarise  # noqa: E402
from archy_harness.markers import Outcome  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all", action="store_true", help="list every test, not just failures")
    opts = parser.parse_args()

    try:
        run = load_run(opts.run)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if opts.json:
        print(json.dumps(run, indent=2))
        return 0

    print(summarise(run))
    print()

    sut = run.get("sut") or {}
    if sut.get("dirty"):
        print(
            "!! the SUT tree was DIRTY when this ran — the result is not reproducible\n"
            f"   from commit {sut.get('commit')} alone\n"
        )

    counts = run.get("counts", {})
    if counts.get(Outcome.NOT_VERIFIED.value) or counts.get(Outcome.INCONCLUSIVE.value):
        print("Note: NOT_VERIFIED and INCONCLUSIVE are not passes. See the entries below.\n")

    interesting = [
        r
        for r in run.get("results", [])
        if opts.all or r["outcome"] != Outcome.PASS.value
    ]
    if not interesting:
        print(f"all {counts.get('PASS', 0)} tests passed")
        return 0

    for result in interesting:
        print(f"[{result['outcome']}] {result['name']}")
        if result.get("reason"):
            print(f"    {result['reason']}")
        if result.get("fix"):
            print(f"    fix: {result['fix']}")
        for evidence in result.get("evidence", []):
            print(f"    evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
