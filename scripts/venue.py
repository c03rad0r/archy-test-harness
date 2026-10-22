#!/usr/bin/env python3
"""Probe which venues are available on this box, and why not.

The point of this script is that a failure to test must be as legible as a failure in a
test. Every unavailable venue names its reason and the command that would fix it.

    ./scripts/venue.py --probe
    ./scripts/venue.py --probe --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS / "lib"))

from archy_harness import venue  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true", help="probe every venue")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args()

    payload = venue.describe_all()

    if opts.json:
        print(json.dumps(payload, indent=2))
    else:
        available = [e for e in payload if e["available"]]
        print(f"venues available on this host: {len(available)}/{len(payload)}\n")
        for entry in payload:
            mark = "OK  " if entry["available"] else "NO  "
            print(f"{mark}{entry['venue']:12s} {entry['description']}")
            if not entry["available"]:
                print(f"      reason: {entry['reason']}")
                if entry["fix"]:
                    print(f"      fix:    {entry['fix']}")
            print(f"      can assert:    {'; '.join(entry['can_assert'][:2])}")
            print(f"      cannot assert: {'; '.join(entry['cannot_assert'][:2])}")
            print()

    # Exit 1 when the two workhorse venues are both unavailable — a caller can gate on this.
    essential = {"inprocess", "localnode"}
    if not any(e["available"] for e in payload if e["venue"] in essential):
        print(
            "error: neither 'inprocess' nor 'localnode' is available — nothing can be tested.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
