"""Run artifacts and provenance.

The rule this module enforces: **a result that cannot name its own provenance is not
evidence.** Every run writes `results/<run_id>/run.json` carrying the full tuple — SUT
commit, dirty flag, venue, harness commit, versions — and every outcome is one of the four
in `markers.Outcome`.

Borrowed from PRTA (`lib/result_publisher.py`, `results/`, `conftest.py:123-126`) with two
corrections PRTA itself documented as mistakes:

* **One run-directory naming scheme**, and it includes the SUT commit:
  `<UTC timestamp>-<short sha>`. PRTA had `test-<ts>` vs `<ts>-<sha>` in two places.
* **No "newest run dir" idiom.** Every command takes an explicit run id. PRTA's
  `ls -dt results/*/ | head -1` is fine interactively and wrong the moment two runs overlap.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from .markers import Outcome


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, timeout=20
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


@dataclasses.dataclass
class SutInfo:
    """Everything needed to reproduce the thing under test."""

    path: str
    commit: str
    short_commit: str
    dirty: bool
    branch: str
    describe: str

    @classmethod
    def inspect(cls, path: Path | None) -> "SutInfo | None":
        if path is None or not (path / ".git").exists():
            return None
        commit = _git(path, "rev-parse", "HEAD")
        status = _git(path, "status", "--porcelain")
        return cls(
            path=str(path),
            commit=commit,
            short_commit=commit[:7],
            dirty=bool(status),
            branch=_git(path, "rev-parse", "--abbrev-ref", "HEAD"),
            describe=_git(path, "describe", "--always", "--dirty", "--tags"),
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class HarnessInfo:
    path: str
    commit: str
    dirty: bool

    @classmethod
    def inspect(cls) -> "HarnessInfo":
        here = Path(__file__).resolve().parents[2]
        commit = _git(here, "rev-parse", "HEAD")
        return cls(
            path=str(here),
            commit=commit,
            dirty=bool(_git(here, "status", "--porcelain")),
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class TestResult:
    name: str
    outcome: Outcome
    duration_s: float
    reason: str = ""
    fix: str = ""
    evidence: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome.value,
            "duration_s": round(self.duration_s, 3),
            "reason": self.reason,
            "fix": self.fix,
            "evidence": list(self.evidence),
        }


@dataclasses.dataclass
class Run:
    """One execution of a profile. The unit of evidence."""

    run_id: str
    profile: str
    venue: str
    sut: SutInfo | None
    harness: HarnessInfo
    started_at: datetime.datetime
    results: list[TestResult] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
    finished_at: datetime.datetime | None = None

    # -- construction ------------------------------------------------------------------

    @classmethod
    def create(cls, profile: str, venue: str, sut_path: Path | None) -> "Run":
        sut = SutInfo.inspect(sut_path)
        harness = HarnessInfo.inspect()
        stamp = _now_utc().strftime("%Y%m%dT%H%M%SZ")
        sha = (sut.short_commit if sut else "nosut")
        return cls(
            run_id=f"{stamp}-{sha}",
            profile=profile,
            venue=venue,
            sut=sut,
            harness=harness,
            started_at=_now_utc(),
        )

    # -- paths -------------------------------------------------------------------------

    @property
    def root(self) -> Path:
        base = Path(os.environ.get("ARCHY_RESULTS_DIR", "results")).expanduser()
        return (base / self.run_id).resolve()

    @property
    def evidence_dir(self) -> Path:
        return self.root / "evidence"

    @property
    def run_json(self) -> Path:
        return self.root / "run.json"

    # -- evidence ----------------------------------------------------------------------

    def attach(self, test_name: str, filename: str, content: str) -> Path:
        """Write an evidence blob for a test. Returns its path relative to the run root."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in test_name)
        target = self.evidence_dir / safe / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        try:
            return target.relative_to(self.root)
        except ValueError:  # pragma: no cover - evidence dir escaped the run root
            return target

    # -- outcomes ----------------------------------------------------------------------

    def add(self, result: TestResult) -> None:
        self.results.append(result)

    def counts(self) -> dict[str, int]:
        counts = {o.value: 0 for o in Outcome}
        for result in self.results:
            counts[result.outcome.value] += 1
        return counts

    def exit_code(self) -> int:
        """0 only when nothing failed and nothing required was left unverified."""
        counts = self.counts()
        if counts[Outcome.FAIL.value] or counts[Outcome.NOT_VERIFIED.value]:
            return 1
        if counts[Outcome.INCONCLUSIVE.value]:
            return 2
        return 0

    # -- serialisation -----------------------------------------------------------------

    def finish(self) -> None:
        self.finished_at = _now_utc()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "run_id": self.run_id,
            "profile": self.profile,
            "venue": self.venue,
            "sut": self.sut.to_dict() if self.sut else None,
            "harness": self.harness.to_dict(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "host": {
                "hostname": platform.node(),
                "platform": platform.platform(),
                "python": sys.version.split()[0],
            },
            "environment": {
                k: v
                for k, v in sorted(os.environ.items())
                if k.startswith("ARCHY_") and not _looks_secret(k, v)
            },
            "counts": self.counts(),
            "exit_code": self.exit_code(),
            "notes": list(self.notes),
            "results": [r.to_dict() for r in self.results],
        }

    def write(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.run_json.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return self.run_json


_SECRET_HINTS = ("TOKEN", "PASSWORD", "SECRET", "KEY", "PASSWD", "MNEMONIC", "NSEC")


def _looks_secret(key: str, value: str) -> bool:
    """Env is stamped into run.json; never let a secret ride along.

    PRTA learned this the hard way (`conftest.py` redaction). A run.json is meant to be
    attached to a PR, so it is effectively public.
    """
    upper = key.upper()
    if any(hint in upper for hint in _SECRET_HINTS):
        return True
    # A long high-entropy token pasted into an innocuous var name
    return len(value) > 80 and not value.startswith(("http://", "https://", "/", "~"))


def load_run(run_id_or_path: str) -> dict[str, Any]:
    """Load run.json by id or by explicit path. Never guesses 'the newest run'."""
    candidate = Path(run_id_or_path).expanduser()
    if candidate.is_file():
        return json.loads(candidate.read_text())
    base = Path(os.environ.get("ARCHY_RESULTS_DIR", "results")).expanduser()
    direct = base / run_id_or_path / "run.json"
    if direct.is_file():
        return json.loads(direct.read_text())
    raise FileNotFoundError(
        f"no run {run_id_or_path!r}; tried {direct}. "
        "Pass an explicit run id or a path to run.json (there is no 'latest' shorthand "
        "on purpose — two overlapping runs make it silently wrong)."
    )


def summarise(run: dict[str, Any]) -> str:
    """One-line-per-run summary for a terminal."""
    counts = run.get("counts", {})
    sut = run.get("sut") or {}
    sha = sut.get("short_commit", "nosut")
    if sut.get("dirty"):
        sha += "+dirty"
    parts = [f"{k}={v}" for k, v in counts.items() if v]
    return (
        f"{run.get('run_id')}  venue={run.get('venue')}  profile={run.get('profile')}  "
        f"sut={sha}  {' '.join(parts) or 'no tests'}  exit={run.get('exit_code')}"
    )
