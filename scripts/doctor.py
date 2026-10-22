#!/usr/bin/env python3
"""Check host prerequisites and report what is missing, with the fix.

    ./scripts/doctor.py

Exit 0 when the smoke profile can run; 1 when it cannot, naming what is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS / "lib"))

from archy_harness import sut as sut_mod  # noqa: E402
from archy_harness import venue  # noqa: E402

OK = "ok  "
BAD = "MISS"


def check(label: str, ok: bool, detail: str, fix: str = "") -> bool:
    print(f"{OK if ok else BAD}  {label:32s} {detail}")
    if not ok and fix:
        print(f"      fix: {fix}")
    return ok


def main() -> int:
    print("archy-test-harness doctor\n")
    all_ok = True

    # python + pytest
    version = sys.version.split()[0]
    all_ok &= check("python", sys.version_info >= (3, 10), version,
                    "use Python 3.10+")
    try:
        import pytest  # noqa: F401

        pytest_ok = True
        detail = f"pytest {pytest.__version__}"
    except ImportError:
        pytest_ok = False
        detail = "not importable"
    all_ok &= check("pytest", pytest_ok, detail,
                    "python -m venv .venv && .venv/bin/pip install pytest pytest-timeout")

    try:
        import pytest_timeout  # noqa: F401

        timeout_ok = True
        detail = "installed"
    except ImportError:
        timeout_ok = False
        detail = "not importable"
    all_ok &= check("pytest-timeout", timeout_ok, detail, ".venv/bin/pip install pytest-timeout")

    # the SUT
    try:
        root = sut_mod.root()
        sut_ok = True
        detail = str(root)
    except sut_mod.SutMissing as exc:
        sut_ok = False
        detail = str(exc).splitlines()[0]
    all_ok &= check("ARCHY_SUT", sut_ok, detail, "export ARCHY_SUT=~/repos/archy")

    # cargo (the non-login-shell trap)
    try:
        cargo = sut_mod.cargo()
        cargo_ok = True
        detail = cargo
    except sut_mod.SutMissing as exc:
        cargo_ok = False
        detail = "not found"
    all_ok &= check("cargo", cargo_ok, detail,
                    'export PATH="$HOME/.cargo/bin:$PATH"  '
                    "# cargo is NOT on a non-login shell's PATH")

    # a built binary (only needed for the localnode venue)
    if sut_ok:
        binary = venue.find_binary(root)
        check("archipelago binary", binary is not None,
              str(binary) if binary else "not built (localnode venue unavailable)",
              "cd $ARCHY_SUT/core && cargo build -p archipelago --bin archipelago")

    # the SUT's own test runner, which we depend on being there
    if sut_ok:
        runner = root / "scripts" / "test-backend-isolated.sh"
        check("SUT isolated test runner", runner.is_file(),
              str(runner) if runner.is_file() else "missing",
              "the SUT mandates this runner; a missing one means a wrong checkout")

    # non-interactive sudo, which the SUT's runner needs
    if shutil.which("sudo"):
        proc = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        check("non-interactive sudo", proc.returncode == 0,
              "available" if proc.returncode == 0 else "password required",
              "the SUT runner's systemd backend needs `sudo -n`; "
              "otherwise set ARCHY_TEST_ISOLATOR=podman + ARCHY_TEST_IMAGE")
    else:
        check("non-interactive sudo", False, "sudo not installed", "")

    print("\nvenue availability:")
    for entry in venue.describe_all():
        mark = OK if entry["available"] else BAD
        print(f"  {mark}  {entry['venue']:12s} {entry['reason'] or 'available'}")
        if not entry["available"] and entry["fix"]:
            print(f"        fix: {entry['fix']}")

    print()
    if all_ok:
        print("smoke profile is runnable:  ./scripts/run-profile.py --profile smoke")
        return 0
    print("fix the MISS lines above before running", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
