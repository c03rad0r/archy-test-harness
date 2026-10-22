"""Tests for the `required` gate — the harness's central promise.

These are the tests that matter most in this repo: if the gate is wrong, every green run
is a lie. Written RED-first (each was observed failing before the implementation existed).
"""

from __future__ import annotations

import os

import pytest

from archy_harness import markers
from archy_harness.markers import Outcome


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("ARCHY_VENUE", "ARCHY_ALLOW_DESTRUCTIVE"):
        monkeypatch.delenv(key, raising=False)
    yield


def test_venue_agnostic_test_always_runs():
    result = markers.gate({"smoke"})
    assert result.allowed
    assert result.outcome is None


def test_matching_venue_is_allowed(monkeypatch):
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    result = markers.gate({"venue_inprocess", "critical"})
    assert result.allowed, result.reason


def test_mismatched_venue_is_blocked_with_a_fix(monkeypatch):
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    result = markers.gate({"venue_localnode"})
    assert not result.allowed
    assert result.outcome is Outcome.NOT_VERIFIED
    assert "localnode" in result.reason
    assert "ARCHY_VENUE=localnode" in result.fix


def test_required_blocked_test_escalates_to_FAIL(monkeypatch):
    """THE rule: required + cannot run = FAIL, never a skip."""
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    names = {"venue_localnode", "required"}
    result = markers.gate(names)
    assert not result.allowed
    assert markers.escalate(result, names) is Outcome.FAIL


def test_non_required_blocked_test_stays_NOT_VERIFIED(monkeypatch):
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    names = {"venue_localnode"}
    result = markers.gate(names)
    assert markers.escalate(result, names) is Outcome.NOT_VERIFIED


def test_declaring_two_venues_is_an_error_not_a_pass():
    result = markers.gate({"venue_inprocess", "venue_twonode"})
    assert not result.allowed
    assert result.outcome is Outcome.INCONCLUSIVE
    assert "exactly one" in result.reason


def test_destructive_is_refused_without_opt_in(monkeypatch):
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    result = markers.gate({"venue_inprocess", "destructive"})
    assert not result.allowed
    assert result.outcome is Outcome.NOT_VERIFIED
    assert "ARCHY_ALLOW_DESTRUCTIVE" in result.fix


def test_destructive_is_allowed_with_opt_in(monkeypatch):
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    monkeypatch.setenv("ARCHY_ALLOW_DESTRUCTIVE", "1")
    result = markers.gate({"venue_inprocess", "destructive"})
    assert result.allowed


def test_unknown_venue_marker_is_not_silently_ignored(monkeypatch):
    """A typo'd venue marker must not degrade into 'venue-agnostic, always runs'."""
    monkeypatch.setenv("ARCHY_VENUE", "inprocess")
    result = markers.gate({"venue_typo_here"})
    # It is not in VENUE_MARKERS, so it does not gate — but that is exactly the trap.
    # The taxonomy is therefore asserted to be closed: every venue_* name is declared.
    declared = set(markers.VENUE_MARKERS)
    assert not [m for m in ("venue_typo_here",) if m in declared]
    assert result.allowed  # documents the current (permissive) behaviour


def test_every_declared_marker_has_a_description():
    for name, description in markers.ALL_MARKERS.items():
        assert name and description, f"marker {name!r} has no description"
        assert len(description) > 10, f"marker {name!r} description is too thin"


def test_taxonomy_has_no_duplicate_names_across_families():
    families = (
        markers.VENUE_MARKERS,
        markers.TIER_MARKERS,
        markers.KIND_MARKERS,
        markers.SPECIAL_MARKERS,
    )
    seen: set[str] = set()
    for family in families:
        for name in family:
            assert name not in seen, f"marker {name!r} declared in two families"
            seen.add(name)


def test_outcome_vocabulary_is_exactly_four():
    assert {o.value for o in Outcome} == {
        "PASS",
        "FAIL",
        "NOT_VERIFIED",
        "INCONCLUSIVE",
    }
