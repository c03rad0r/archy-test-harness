"""Pytest plugin: the `required` gate, evidence capture, and outcome reporting.

This is the module that makes the harness's central promise true: **a test marked
`required` that cannot run is FAILED — NOT VERIFIED, never SKIPPED.**

Design note — the thing PRTA got wrong and this avoids: PRTA put its marker semantics in a
1568-line root `conftest.py` with interleaved hooks whose ordering was load-bearing and only
explained in comments. Here the gate is one function (`markers.gate`) called from one hook,
and the taxonomy is declared in `markers.py` next to its consumers.

Outcome mapping:
  * test passes                      -> PASS
  * test raises                      -> FAIL
  * gate blocks (venue unavailable)  -> NOT_VERIFIED, escalated to FAIL if `required`
  * teardown detects interference    -> INCONCLUSIVE (overrides PASS)
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from . import markers
from .evidence import Run, TestResult

# The active run, set by the run-profile entry point before pytest starts.
_RUN: Run | None = None


def set_run(run: Run) -> None:
    global _RUN
    _RUN = run


def get_run() -> Run | None:
    return _RUN


def pytest_configure(config: pytest.Config) -> None:
    """Declare markers, and create the Run from env if the entry point has not set one.

    The Run is created HERE rather than being constructed by the launcher and injected,
    because injecting it requires importing this module before pytest starts — which makes
    pytest warn that the module is already imported and cannot be assert-rewritten. The env
    contract is the same one the venue uses, so there is one way to say "this is the run".
    """
    for name, description in markers.ALL_MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {description}")

    if _RUN is None:
        import datetime
        import os
        from pathlib import Path

        run_id = os.environ.get("ARCHY_HARNESS_RUN_ID")
        if run_id:
            from .evidence import HarnessInfo, SutInfo

            sut_env = os.environ.get("ARCHY_SUT")
            run = Run(
                run_id=run_id,
                profile=os.environ.get("ARCHY_PROFILE", "unknown"),
                venue=markers.enabled_venue(),
                sut=SutInfo.inspect(Path(sut_env).expanduser()) if sut_env else None,
                harness=HarnessInfo.inspect(),
                started_at=datetime.datetime.now(datetime.timezone.utc),
            )
            set_run(run)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Annotate each item with its gate decision. Never silently drop an item."""
    for item in items:
        names = {m.name for m in item.iter_markers()}
        item._archy_markers = names  # type: ignore[attr-defined]
        result = markers.gate(names)
        item._archy_gate = result  # type: ignore[attr-defined]
        if not result.allowed:
            outcome = markers.escalate(result, names)
            reason = result.reason
            fix = result.fix
            if outcome is markers.Outcome.FAIL:
                item.add_marker(
                    pytest.mark.skip(reason=f"REQUIRED-BUT-UNRUNNABLE: {reason} ({fix})")
                )
                item._archy_forced_fail = (reason, fix)  # type: ignore[attr-defined]
            else:
                item.add_marker(pytest.mark.skip(reason=f"not verified: {reason}"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Capture the real outcome, including the required-escalation and interference."""
    outcome = yield
    report = outcome.get_result()

    # A skip we injected for a `required` test is not a skip — it is a failure.
    forced = getattr(item, "_archy_forced_fail", None)
    if forced and report.when == "setup" and report.skipped:
        reason, fix = forced
        report.outcome = "failed"
        report.longrepr = (
            f"NOT VERIFIED (required): {reason}\n"
            f"fix: {fix}\n"
            "This test is marked `required`, so it may not be skipped. Either make its "
            "venue available or remove the `required` marker and say why in the report."
        )

    # Record at most once per item: prefer the call phase, but a setup-phase skip or error
    # IS the result (the test never ran). Missing this is how a NOT_VERIFIED test vanishes
    # from the counts while the summary still says "1 skipped".
    is_result_phase = report.when == "call" or report.skipped or report.failed
    if not is_result_phase:
        return
    if report.when == "teardown" and not report.failed:
        return

    run = get_run()
    if run is None:
        return

    # Never double-record the same item.
    if getattr(item, "_archy_recorded", False):
        return
    item._archy_recorded = True  # type: ignore[attr-defined]

    names = getattr(item, "_archy_markers", set())
    interference = getattr(item, "_archy_interference", None)

    if report.skipped:
        result_outcome = markers.Outcome.NOT_VERIFIED
        reason = str(report.longrepr) if report.longrepr else "skipped"
        fix = ""
    elif report.failed:
        result_outcome = markers.Outcome.FAIL
        reason = _first_line(str(report.longrepr)) if report.longrepr else "failed"
        fix = ""
    elif interference:
        # The test passed but the environment was not trustworthy. Never a pass.
        result_outcome = markers.Outcome.INCONCLUSIVE
        reason = f"interference detected: {interference}"
        fix = "re-run on a quiescent box; see docs/BLOCKERS.md on silent mutators"
    else:
        result_outcome = markers.Outcome.PASS
        reason = ""
        fix = ""

    run.add(
        TestResult(
            name=item.nodeid,
            outcome=result_outcome,
            duration_s=getattr(report, "duration", 0.0),
            reason=reason,
            fix=fix,
            evidence=list(getattr(item, "_archy_evidence", [])),
        )
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    run = get_run()
    if run is None:
        return
    run.finish()
    run.write()


def pytest_terminal_summary(terminalreporter, exitstatus: int, config) -> None:
    run = get_run()
    if run is None:
        return
    counts = run.counts()
    terminalreporter.write_sep("=", "archy-test-harness outcome")
    for outcome in markers.Outcome:
        count = counts[outcome.value]
        if count:
            terminalreporter.write_line(f"  {outcome.value:14s} {count}")
    terminalreporter.write_line(f"  run id         {run.run_id}")
    terminalreporter.write_line(f"  exit code      {run.exit_code()}")
    if counts[markers.Outcome.NOT_VERIFIED.value]:
        terminalreporter.write_line(
            "  NOT VERIFIED means a test could not run. If it was not `required`, that is "
            "recorded; if it was, it is counted as FAIL."
        )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("_"):
            return stripped[:400]
    return text[:400]


# --------------------------------------------------------------------------------------
# fixtures available to tests
# --------------------------------------------------------------------------------------


@pytest.fixture
def archy_run() -> Run:
    run = get_run()
    if run is None:
        pytest.fail(
            "no active run — tests must be launched through scripts/run-profile.py, "
            "which creates the run and its evidence directory"
        )
    return run


@pytest.fixture
def attach_evidence(archy_run: Run):
    """Return a callable that records an evidence blob against the current test."""

    def _attach(filename: str, content: str, item: Any = None) -> None:
        name = item.name if item is not None else "unattributed"
        rel = archy_run.attach(name, filename, content)
        current = getattr(item, "_archy_evidence", None) if item else None
        if current is not None:
            current.append(str(rel))

    return _attach


@pytest.fixture
def watch_interference(request: pytest.FixtureRequest):
    """Mark the test INCONCLUSIVE if a mid-run change is observed.

    Model: PRTA's box-identity guard, which pins router uptime + daemon pids at phase 0 and
    re-reads them at every boundary, failing the run hard if any moves. A service bounce
    mid-run invalidates the result — it does not fail it, it makes it untrustworthy, and
    those are different outcomes.
    """

    def _flag(reason: str) -> None:
        request.node._archy_interference = reason  # type: ignore[attr-defined]

    return _flag
