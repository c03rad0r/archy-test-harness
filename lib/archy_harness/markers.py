"""Marker taxonomy and the `required` gate.

Borrowed from PRTA (`pytest.ini:6-43` + `conftest.py:1367-1478`) but **deliberately
simplified**: PRTA grew ~35 markers of which several are vestigial or near-synonyms
(`hardware` / `physical_hardware` / `physical_only`; `cloud_lab_only` with zero users).
This module declares a small taxonomy where every marker has exactly one consumer, and the
gate for each marker lives next to its declaration — the thing PRTA got wrong by putting
semantics 1400 lines away from the declaration.

THE CENTRAL RULE
----------------
A test marked `required` that cannot run is **FAILED — NOT VERIFIED**, never skipped.

Without this, "green" means "we didn't crash". With it, "green" means "everything we claimed
to verify, we verified". A skip is only legal for a test that is not required, and the
report names every skip.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from typing import Any


class Outcome(str, enum.Enum):
    """The four-outcome vocabulary. Every result is exactly one of these.

    PRTA conflated some of these; separating them is what lets a release gate consume a
    result. In particular INCONCLUSIVE (the test ran but its setup was not trustworthy)
    must never read as PASS.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    #: Could not run — venue unavailable, prerequisite missing. For `required` tests this
    #: is escalated to FAIL by the gate.
    NOT_VERIFIED = "NOT_VERIFIED"
    #: Ran, but the environment was not trustworthy (service bounced mid-run, interference
    #: detected). Never a pass. PRTA's box-identity guard is the model.
    INCONCLUSIVE = "INCONCLUSIVE"


# --------------------------------------------------------------------------------------
# The marker taxonomy. Four families, each with one job.
# --------------------------------------------------------------------------------------

#: Family 1 — what the test needs. These gate on a capability probe; a miss on a
#: `required` test becomes NOT_VERIFIED.
VENUE_MARKERS = {
    "venue_inprocess": "needs only in-process ApiHandler (no listener)",
    "venue_localnode": "needs the real binary listening on loopback",
    "venue_twonode": "needs two nodes joined over FIPS",
    "venue_hardware": "needs a real installed Archipelago box (podman + apps)",
}

#: Family 2 — seriousness / cost. These select profiles; they never skip on their own.
TIER_MARKERS = {
    "smoke": "seconds; the run a human does before trusting a branch",
    "critical": "the release-relevant subset; minutes",
    "extended": "slow or exhaustive; not in the default profile",
}

#: Family 3 — what the test is for. Documentation, and the axis a reviewer filters on.
KIND_MARKERS = {
    "regression": "pins a past bug or a contract that regressed before",
    "happy": "the flow works as designed",
    "failure": "a broken input or service is refused loudly and bounded",
}

#: Family 4 — the escalation marker. This is the one that matters.
SPECIAL_MARKERS = {
    "required": (
        "this test's outcome is release-relevant. If it cannot run, the result is "
        "FAILED — NOT VERIFIED, never SKIPPED."
    ),
    "destructive": "mutates state beyond a normal service toggle; needs explicit opt-in",
}

ALL_MARKERS = {**VENUE_MARKERS, **TIER_MARKERS, **KIND_MARKERS, **SPECIAL_MARKERS}


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    outcome: Outcome | None
    reason: str = ""
    fix: str = ""


def enabled_venue() -> str:
    return os.environ.get("ARCHY_VENUE", "inprocess")


def destructive_allowed() -> bool:
    return os.environ.get("ARCHY_ALLOW_DESTRUCTIVE") == "1"


def gate(marker_names: set[str]) -> GateResult:
    """Decide whether a test may run, and what its outcome is if it may not.

    Called once per test by the pytest hook. Returns:
      * allowed=True  -> run it
      * allowed=False -> do not run; `outcome` is NOT_VERIFIED and the caller decides
                         whether to escalate to FAIL based on `required`.
    """
    required = "required" in marker_names

    # Destructive work needs an explicit opt-in. Refusing is not a failure of the test.
    if "destructive" in marker_names and not destructive_allowed():
        return GateResult(
            allowed=False,
            outcome=Outcome.NOT_VERIFIED,
            reason="destructive test without ARCHY_ALLOW_DESTRUCTIVE=1",
            fix="export ARCHY_ALLOW_DESTRUCTIVE=1 to include destructive coverage",
        )

    declared = sorted(
        m for m in marker_names if m in VENUE_MARKERS
    )
    if len(declared) > 1:
        return GateResult(
            allowed=False,
            outcome=Outcome.INCONCLUSIVE,
            reason=f"test declares {len(declared)} venues {declared}; exactly one is allowed",
            fix="remove all but the venue the test actually needs",
        )

    if not declared:
        # No venue marker => venue-agnostic (pure logic). Always runnable.
        return GateResult(allowed=True, outcome=None)

    wanted = declared[0].removeprefix("venue_")
    active = enabled_venue()
    if wanted != active:
        reason = f"needs venue {wanted!r}, running on {active!r}"
        return GateResult(
            allowed=False,
            outcome=Outcome.NOT_VERIFIED,
            reason=reason,
            fix=f"ARCHY_VENUE={wanted}",
        )

    # Same venue — but is it actually available on this host?
    from . import venue as venue_mod

    ok, reason, fix = venue_mod.probe(active)
    if not ok:
        return GateResult(allowed=False, outcome=Outcome.NOT_VERIFIED, reason=reason, fix=fix)

    return GateResult(allowed=True, outcome=None)


def escalate(result: GateResult, marker_names: set[str]) -> Outcome:
    """Turn a blocked gate into the final outcome for this test.

    This is the whole rule in one function: required => FAIL, otherwise NOT_VERIFIED.
    """
    if result.outcome is None:  # pragma: no cover - defensive
        return Outcome.PASS
    if "required" in marker_names and result.outcome is Outcome.NOT_VERIFIED:
        return Outcome.FAIL
    return result.outcome


def describe() -> dict[str, Any]:
    """Machine-readable taxonomy, for `scripts/run-profile.py --markers`."""
    return {
        "venues": VENUE_MARKERS,
        "tiers": TIER_MARKERS,
        "kinds": KIND_MARKERS,
        "special": SPECIAL_MARKERS,
        "active_venue": enabled_venue(),
        "destructive_allowed": destructive_allowed(),
    }
