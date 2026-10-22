"""Tests for the venue seam, the evidence artifacts, and the SUT locator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archy_harness import evidence, sut, venue
from archy_harness.markers import Outcome


# --------------------------------------------------------------------------------------
# venue
# --------------------------------------------------------------------------------------


def test_every_venue_declares_what_it_can_and_cannot_assert():
    """An honest capability table is the point of the seam. No empty entries."""
    for name, spec in venue.VENUES.items():
        assert spec.can_assert, f"{name} declares nothing it can assert"
        assert spec.cannot_assert, f"{name} declares no limits — that is not honest"


def test_probe_reports_a_reason_and_a_fix_when_unavailable(monkeypatch):
    monkeypatch.delenv("ARCHY_SUT", raising=False)
    ok, reason, fix = venue.probe("localnode")
    assert not ok
    assert reason
    assert fix, "an unavailable venue must tell the operator how to fix it"


def test_require_raises_with_the_reason(monkeypatch):
    monkeypatch.delenv("ARCHY_SUT", raising=False)
    with pytest.raises(venue.VenueUnavailable) as excinfo:
        venue.require("localnode")
    assert excinfo.value.venue == "localnode"
    assert "ARCHY_SUT" in excinfo.value.reason


def test_unknown_venue_is_rejected_not_defaulted():
    ok, reason, _ = venue.probe("no-such-venue")
    assert not ok
    assert "unknown venue" in reason


def test_env_contract_has_one_authoritative_spelling(tmp_path):
    cfg = venue.VenueConfig(
        kind="inprocess",
        base_url="http://127.0.0.1:1234",
        data_dir=tmp_path,
    )
    env = cfg.to_env()
    assert env["ARCHY_VENUE"] == "inprocess"
    assert env["ARCHY_BASE_URL"] == "http://127.0.0.1:1234"
    assert env["ARCHY_DATA_DIR"] == str(tmp_path)
    # Nothing is exported that was not set.
    assert "ARCHY_BINARY" not in env
    assert "ARCHY_FIPS_DNS" not in env


def test_apply_overwrites_by_default_but_respects_when_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHY_BASE_URL", "http://stale:1")
    cfg = venue.VenueConfig(kind="inprocess", base_url="http://fresh:2", data_dir=tmp_path)

    venue.apply(cfg, respect_existing=True)
    import os

    assert os.environ["ARCHY_BASE_URL"] == "http://stale:1", "explicit operator choice must win"

    venue.apply(cfg, respect_existing=False)
    assert os.environ["ARCHY_BASE_URL"] == "http://fresh:2", (
        "an authoritative venue must clear a stale target, or the run silently tests "
        "the wrong box"
    )


def test_tcp_probe_never_uses_icmp():
    """Liveness is a TCP connect. Asserted by behaviour: a closed port is False, fast."""
    import time

    start = time.monotonic()
    assert venue.tcp_open("127.0.0.1", 1, timeout=0.5) is False
    assert time.monotonic() - start < 3.0


def test_describe_all_is_machine_readable():
    payload = venue.describe_all()
    assert isinstance(payload, list)
    names = {entry["venue"] for entry in payload}
    assert names == set(venue.VENUES)
    for entry in payload:
        assert set(entry) >= {"venue", "available", "reason", "fix", "can_assert"}


# --------------------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------------------


def test_run_id_includes_the_sut_commit(tmp_path):
    """A run id that does not name the SUT cannot be reproduced."""
    run = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=Path("/nonexistent"))
    assert "nosut" in run.run_id or len(run.run_id.split("-")[-1]) >= 7


def test_exit_code_semantics(tmp_path):
    run = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    assert run.exit_code() == 0, "an empty run is not a failure, it is empty"

    run.add(evidence.TestResult("a", Outcome.PASS, 0.1))
    assert run.exit_code() == 0

    run.add(evidence.TestResult("b", Outcome.NOT_VERIFIED, 0.1))
    assert run.exit_code() == 1, "unverified work must not exit 0"

    run2 = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    run2.add(evidence.TestResult("c", Outcome.INCONCLUSIVE, 0.1))
    assert run2.exit_code() == 2, "inconclusive is distinct from fail"

    run3 = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    run3.add(evidence.TestResult("d", Outcome.FAIL, 0.1))
    assert run3.exit_code() == 1


def test_secrets_are_never_stamped_into_run_json(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHY_API_TOKEN", "super-secret-value")
    monkeypatch.setenv("ARCHY_LONG_BLOB", "x" * 200)
    run = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    payload = run.to_dict()
    blob = json.dumps(payload)
    assert "super-secret-value" not in blob
    assert "x" * 200 not in blob


def test_run_json_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHY_RESULTS_DIR", str(tmp_path))
    run = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    run.add(evidence.TestResult("t", Outcome.PASS, 0.5, evidence=["evidence/t/x.txt"]))
    run.finish()
    path = run.write()
    assert path.is_file()

    loaded = json.loads(path.read_text())
    assert loaded["run_id"] == run.run_id
    assert loaded["counts"]["PASS"] == 1
    assert loaded["schema"] == 1
    assert "harness" in loaded and loaded["harness"]["commit"] is not None


def test_evidence_attachment_lands_under_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHY_RESULTS_DIR", str(tmp_path))
    run = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    rel = run.attach("tests/media/test_x.py::test_y", "headers.txt", "HTTP/1.1 206")
    written = run.root / rel
    assert written.is_file()
    assert written.read_text() == "HTTP/1.1 206"


def test_evidence_filename_is_sanitised(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHY_RESULTS_DIR", str(tmp_path))
    run = evidence.Run.create(profile="smoke", venue="inprocess", sut_path=None)
    rel = run.attach("weird/name::with spaces", "out.txt", "x")
    assert " " not in str(rel)
    assert (run.root / rel).is_file()


def test_load_run_refuses_to_guess_the_latest(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHY_RESULTS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError) as excinfo:
        evidence.load_run("does-not-exist")
    assert "no 'latest' shorthand" in str(excinfo.value)


def test_summarise_marks_a_dirty_sut():
    payload = {
        "run_id": "r",
        "venue": "inprocess",
        "profile": "smoke",
        "sut": {"short_commit": "abc1234", "dirty": True},
        "counts": {"PASS": 2},
        "exit_code": 0,
    }
    text = evidence.summarise(payload)
    assert "abc1234+dirty" in text


# --------------------------------------------------------------------------------------
# sut
# --------------------------------------------------------------------------------------


def test_missing_sut_explains_the_fix(monkeypatch):
    monkeypatch.delenv("ARCHY_SUT", raising=False)
    with pytest.raises(sut.SutMissing) as excinfo:
        sut.root()
    assert "ARCHY_SUT" in str(excinfo.value)


def test_sut_pointed_at_a_non_checkout_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHY_SUT", str(tmp_path))
    with pytest.raises(sut.SutMissing) as excinfo:
        sut.root()
    assert "core/Cargo.toml" in str(excinfo.value), (
        "the error must name the core/ workspace trap, or it reads like a broken clone"
    )


def test_cargo_is_found_even_when_path_is_bare(monkeypatch):
    """The non-login-shell trap: cargo lives in ~/.cargo/bin and is not on PATH."""
    monkeypatch.setenv("PATH", "/nonexistent")
    found = sut.cargo()
    assert "cargo" in found
