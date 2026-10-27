"""Tests for the harness's own promises.

These are meta-tests: they check that the things the README and docs claim are actually true
of the code. If one of these fails, the documentation is lying.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (HARNESS / rel).read_text()


# --------------------------------------------------------------------------------------
# README and docs must not promise things the code does not do
# --------------------------------------------------------------------------------------


def test_every_script_named_in_the_readme_exists():
    readme = read("README.md")
    named = set(re.findall(r"\./scripts/([a-z0-9_-]+\.py)", readme))
    assert named, "the README names no scripts — it should document the command surface"
    for script in named:
        assert (HARNESS / "scripts" / script).is_file(), f"README names {script}, which is missing"


def test_every_profile_named_in_the_readme_exists():
    readme = read("README.md")
    named = set(re.findall(r"--profile ([a-z0-9_-]+)", readme))
    assert named
    for profile in named:
        assert (HARNESS / "profiles" / f"{profile}.yaml").is_file(), (
            f"README names profile {profile}, which is missing"
        )


def test_root_conftest_stays_thin():
    """The whole point of not repeating PRTA's 1568-line conftest.py."""
    lines = read("conftest.py").splitlines()
    assert len(lines) < 40, (
        f"root conftest.py is {len(lines)} lines. PRTA's is 1568 and its own extract calls "
        "that a mistake. Logic belongs in archy_harness.pytest_plugin."
    )


def test_there_is_exactly_one_canonical_runner():
    """PRTA ended up with four live entry layers; its extract calls that a mistake."""
    scripts = {p.name for p in (HARNESS / "scripts").glob("*.py")}
    runners = {s for s in scripts if "run" in s}
    assert runners == {"run-profile.py"}, (
        f"more than one runner: {runners}. There must be exactly one canonical entry point."
    )


def test_makefile_is_a_thin_alias_surface():
    """The Makefile is now deliberate fleet IaC; it must stay a thin alias surface."""
    makefile = HARNESS / "Makefile"
    assert makefile.is_file(), "Makefile is the documented fleet entry point"
    text = makefile.read_text()
    for target in ("test", "build", "iso", "vm-install", "node-check", "media-soak"):
        assert re.search(rf"^{target}:", text, re.M), f"Makefile is missing target {target}"
    assert "run-profile.py" in text, "make test must route through the canonical runner"
    assert "scripts/remote.sh" in text, "HOST= must route through remote.sh"


def test_documented_blockers_are_numbered_and_unique():
    text = read("docs/BLOCKERS.md")
    ids = re.findall(r"^## (B\d+) ", text, re.M)
    assert ids, "BLOCKERS.md has no numbered blockers"
    assert len(ids) == len(set(ids)), f"duplicate blocker ids: {ids}"


def test_every_venue_is_documented():
    from archy_harness import venue

    doc = read("docs/VENUES.md")
    for name in venue.VENUES:
        assert f"`{name}`" in doc, f"venue {name} is not documented in docs/VENUES.md"


def test_every_blocker_that_names_code_uses_a_file_line_citation():
    """A blocker without a citation cannot be re-checked; it becomes folklore.

    Accepts any `path:line` (or `path:line-line`) citation, not only Rust — a blocker about
    CI config legitimately cites a workflow file. A blocker about *absence* cites the files
    that were checked; that also counts, as long as it names them.
    """
    text = read("docs/BLOCKERS.md")
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    checked = 0
    for section in sections:
        if not section.startswith("B"):
            continue
        title = section.splitlines()[0]
        # path:line, path:line-line, or a backticked file path being reported as absent
        has_citation = bool(
            re.search(r"[\w./-]+\.(rs|py|sh|yml|yaml|toml|md|json|mjs):\d+", section)
            or re.search(r"`[\w./-]+\.(rs|py|sh|yml|yaml|toml|md|json|mjs)`", section)
        )
        assert has_citation, (
            f"blocker {title!r} cites no file:line and names no file. A blocker that cannot "
            "be re-checked is folklore, not a blocker."
        )
        checked += 1
    assert checked >= 5, f"only {checked} blockers found; expected the known set"


def test_readme_documents_the_required_rule():
    readme = read("README.md")
    assert "NOT VERIFIED" in readme
    assert "required" in readme


def test_handover_documents_the_boundaries_requirement():
    doc = read("docs/OPERATOR-HANDOVER.md")
    assert "boundaries" in doc.lower()
    assert "coverage" in doc.lower(), (
        "the handover must say that no coverage percentage is claimed"
    )
