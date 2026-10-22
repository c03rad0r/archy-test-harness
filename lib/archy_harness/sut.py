"""Locating, identifying and building the SUT (Archipelago).

The SUT is never vendored. `ARCHY_SUT` points at a checkout. Everything here is about
answering "which Archipelago is this, and can I run it" without guessing.

Two SUT-specific traps, both measured on this host and recorded in the SUT handover:

1. **The cargo workspace root is `core/`, not the repo root.** `cargo check` from the repo
   root fails with "could not find Cargo.toml", which reads like a broken clone.
2. **`cargo` is not on the PATH of a non-login shell.** It lives in `~/.cargo/bin` and is
   only exported by `.bashrc`/`.profile`. A dispatcher/worker shell gets
   "cargo: command not found" (127), which reads like a missing toolchain.

Both are handled here so a test author never hits them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class SutMissing(RuntimeError):
    """No usable SUT checkout. Carries the fix, not just the complaint."""


def root() -> Path:
    raw = os.environ.get("ARCHY_SUT")
    if not raw:
        raise SutMissing(
            "ARCHY_SUT is not set. Point it at an Archipelago checkout:\n"
            "  export ARCHY_SUT=~/repos/archy"
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise SutMissing(f"ARCHY_SUT={path} is not a directory")
    if not (path / "core" / "Cargo.toml").exists():
        raise SutMissing(
            f"ARCHY_SUT={path} has no core/Cargo.toml — is this really an Archipelago "
            "checkout? The cargo workspace root is core/, not the repo root."
        )
    return path


def core() -> Path:
    return root() / "core"


def cargo() -> str:
    """Locate cargo even when PATH has not been sourced (the non-login-shell trap)."""
    found = shutil.which("cargo")
    if found:
        return found
    for candidate in (
        Path.home() / ".cargo" / "bin" / "cargo",
        Path("/usr/local/cargo/bin/cargo"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SutMissing(
        "cargo not found. It is usually installed at ~/.cargo/bin/cargo but not on the "
        "PATH of a non-login shell. Add: export PATH=\"$HOME/.cargo/bin:$PATH\""
    )


def build(*, release: bool = False, timeout: int = 1800) -> Path:
    """Build the archipelago binary. Slow; call it explicitly, never from a test."""
    env = dict(os.environ)
    env["PATH"] = f"{Path.home() / '.cargo' / 'bin'}:{env.get('PATH', '')}"
    args = [cargo(), "build", "-p", "archipelago", "--bin", "archipelago"]
    if release:
        args.append("--release")
    proc = subprocess.run(
        args, cwd=core(), capture_output=True, text=True, timeout=timeout, env=env
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).splitlines()[-25:])
        raise SutMissing(f"cargo build failed (exit {proc.returncode}):\n{tail}")
    from .venue import find_binary

    binary = find_binary(root())
    if binary is None:  # pragma: no cover - build succeeded but no artifact
        raise SutMissing("build succeeded but no binary was found under target/")
    return binary


def clippy_baseline() -> dict[str, object]:
    """Measure the PRE-EXISTING clippy failure count for the smallest accepted package.

    This exists because the SUT's own `CONTRIBUTING.md:24` requires
    `cargo clippy -- -D warnings` to pass, and on a pristine `fcc18b3` checkout it does
    not: 123 errors, exit 101. Any lint gate on this repo must therefore be a RATCHET
    ("must not increase"), not an absolute. Measured 2026-10-22.
    """
    env = dict(os.environ)
    env["PATH"] = f"{Path.home() / '.cargo' / 'bin'}:{env.get('PATH', '')}"
    proc = subprocess.run(
        [cargo(), "clippy", "-p", "archipelago", "--bin", "archipelago"],
        cwd=core(),
        capture_output=True,
        text=True,
        timeout=1800,
        env=env,
    )
    combined = (proc.stderr or "") + (proc.stdout or "")
    warnings = sum(1 for line in combined.splitlines() if line.startswith("warning:"))
    errors = sum(1 for line in combined.splitlines() if line.startswith("error"))
    return {
        "exit_code": proc.returncode,
        "warning_lines": warnings,
        "error_lines": errors,
        # The headline number: how many items `-D warnings` would reject.
        "deny_warnings_errors": errors,
    }
