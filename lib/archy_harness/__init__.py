"""archy-test-harness — an end-to-end test harness for Archipelago.

Public surface (kept small on purpose):

    from archy_harness import venue, markers, evidence, sut, proc

The harness's two promises:

1. A test marked `required` that cannot run is FAILED — NOT VERIFIED, never SKIPPED.
2. A result names its own provenance (SUT commit, venue, harness commit) or it is not
   evidence.

See README.md and docs/ for the design, and docs/BLOCKERS.md for what is honestly not
covered yet.
"""

from . import evidence, markers, proc, sut, venue

__all__ = ["evidence", "markers", "proc", "sut", "venue"]
__version__ = "0.1.0"
