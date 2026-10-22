#!/usr/bin/env python3
"""The canonical entry point. Everything else is an alias to this.

There is exactly ONE runner (a lesson from PRTA, which ended up with four entry layers —
Make, scripts, a pymake registry and profiles — all alive at once).

    ./scripts/run-profile.py --profile smoke
    ./scripts/run-profile.py --profile smoke --list          # select, do not run
    ./scripts/run-profile.py --profile media --venue localnode
    ./scripts/run-profile.py --markers                       # print the taxonomy
    ./scripts/run-profile.py --venues                        # probe availability
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS / "lib"))

from archy_harness import markers, venue as venue_mod  # noqa: E402
from archy_harness.evidence import Run, summarise  # noqa: E402
from archy_harness.sut import SutMissing, root as sut_root  # noqa: E402

PROFILES_DIR = HARNESS / "profiles"


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))
        raise SystemExit(f"unknown profile {name!r}. Available: {', '.join(available)}")
    text = path.read_text()
    try:
        import yaml  # type: ignore
    except ImportError:
        return _mini_yaml(text)
    return yaml.safe_load(text)


def _mini_yaml(text: str) -> dict:
    """A tiny subset parser so the harness has no hard dependency on PyYAML.

    Handles the flat `key: value` and `key:\n  - item` shapes the profiles actually use.
    Anything richer raises rather than silently mis-parsing.
    """
    out: dict = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is None:
                raise SystemExit("malformed profile: list item with no key")
            out.setdefault(current, []).append(line[4:].strip().strip('"\''))
            continue
        if line.startswith((" ", "\t")):
            raise SystemExit(f"unsupported profile indentation: {raw!r}")
        if ":" not in line:
            raise SystemExit(f"malformed profile line: {raw!r}")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value:
            out[key] = value.strip('"\'')
            current = None
        else:
            current = key
            out[key] = []
    return out


def build_pytest_args(profile: dict, extra: list[str]) -> list[str]:
    args = ["-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
    # NOTE: the plugin is registered by the root conftest.py via `pytest_plugins`.
    # Passing `-p archy_harness.pytest_plugin` here as well double-registers it and pytest
    # warns "Module already imported so cannot be rewritten". Do not add it back.

    for marker in profile.get("markers", []):
        args += ["-m", marker]
    for path in profile.get("paths", []):
        args.append(path)
    timeout = profile.get("timeout")
    if timeout:
        args += ["--timeout", str(timeout)]
    args += profile.get("extra_args", [])
    args += extra
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="smoke")
    parser.add_argument("--venue", default=None, help="override ARCHY_VENUE")
    parser.add_argument("--list", action="store_true", help="select tests, do not run them")
    parser.add_argument("--markers", action="store_true", help="print the marker taxonomy")
    parser.add_argument("--venues", action="store_true", help="probe venue availability")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("pytest_args", nargs="*", help="passed through to pytest")
    opts = parser.parse_args()

    if opts.markers:
        payload = markers.describe()
        print(json.dumps(payload, indent=2) if opts.json else _fmt_markers(payload))
        return 0

    if opts.venues:
        payload = venue_mod.describe_all()
        print(json.dumps(payload, indent=2) if opts.json else _fmt_venues(payload))
        return 0

    profile = load_profile(opts.profile)
    venue_name = opts.venue or profile.get("venue") or "inprocess"
    os.environ["ARCHY_VENUE"] = venue_name

    # Fail fast and loudly if the SUT is missing — a test run without a SUT is meaningless.
    try:
        sut_path = sut_root()
    except SutMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if opts.list:
        args = build_pytest_args(profile, opts.pytest_args + ["--collect-only"])
        return subprocess.run(args, cwd=HARNESS).returncode

    run = Run.create(profile=opts.profile, venue=venue_name, sut_path=sut_path)

    env = dict(os.environ)
    env.update(venue_mod.env_contract())
    env["PYTHONPATH"] = f"{HARNESS / 'lib'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    # The plugin creates its own Run from these two vars (see pytest_plugin.pytest_configure).
    # The id is minted here so the parent knows where to look for run.json.
    env["ARCHY_HARNESS_RUN_ID"] = run.run_id
    env["ARCHY_PROFILE"] = opts.profile

    # Pre-create the run directory so a crash still leaves a trace at the expected path.
    run.root.mkdir(parents=True, exist_ok=True)
    run.evidence_dir.mkdir(parents=True, exist_ok=True)

    args = build_pytest_args(profile, opts.pytest_args)
    argv = [sys.executable, "-m", "pytest", *args]
    print(f"→ run {run.run_id}  profile={opts.profile}  venue={venue_name}")
    proc = subprocess.run(argv, cwd=HARNESS, env=env)

    # The child wrote run.json; re-read and summarise so the parent reports the truth.
    try:
        from archy_harness.evidence import load_run

        payload = load_run(run.run_id)
        print(summarise(payload))
        # A run that collected nothing is NOT a pass. Reporting exit=0 for zero tests is
        # exactly the "green means nothing" failure this harness exists to prevent.
        if not payload.get("results"):
            print(
                "error: the run collected ZERO tests — this is not a pass. Check that the "
                "profile's paths and markers select something (`--list`).",
                file=sys.stderr,
            )
            return 4
        return int(payload.get("exit_code", proc.returncode))
    except FileNotFoundError:
        print(
            f"error: pytest exited {proc.returncode} but wrote no run.json — the run is "
            "unrecorded and therefore not evidence.",
            file=sys.stderr,
        )
        return proc.returncode


def _fmt_markers(payload: dict) -> str:
    lines = [f"active venue: {payload['active_venue']}",
             f"destructive allowed: {payload['destructive_allowed']}", ""]
    for family in ("venues", "tiers", "kinds", "special"):
        lines.append(f"{family}:")
        for name, desc in payload[family].items():
            lines.append(f"  {name:20s} {desc}")
        lines.append("")
    return "\n".join(lines)


def _fmt_venues(payload: list[dict]) -> str:
    lines = []
    for entry in payload:
        mark = "OK  " if entry["available"] else "NO  "
        lines.append(f"{mark}{entry['venue']:12s} {entry['description']}")
        if not entry["available"]:
            lines.append(f"      reason: {entry['reason']}")
            if entry["fix"]:
                lines.append(f"      fix:    {entry['fix']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
