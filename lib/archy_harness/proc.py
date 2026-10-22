"""Process fixtures — run the real binary, bounded.

Borrowed in spirit from the SUT's own `core/archipelago/src/process_fixture.rs` (533 lines),
which already proves the highest-value pattern available: spawn the real binary, drive the
whole purchase protocol against a fake mint over loopback, including restart and lost-reply
recovery. **Do not rebuild that.** This module is for the cases it does not cover — mainly
"is the listener up and does the wire behave".

Rules encoded here, each from a measured failure:

* **Poll a readiness predicate, never `sleep`.** A fixed sleep is either flaky or slow and
  the timing behaviour is untested either way. PRTA learned this (`conftest.py:842-859`
  patched sleeps away, which meant the timing was never exercised).
* **Bounded shutdown, then SIGKILL.** A node that ignores SIGTERM must not hang the suite.
* **Capture stdout+stderr to the run's evidence dir**, always — a crashed node with no log
  is an unattributable failure.
* **Bind ephemeral ports and read them back.** Hardcoding 5678 collides with a real node on
  the box and with a concurrent run.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from . import sut as sut_mod
from . import venue as venue_mod


class NodeFailedToStart(RuntimeError):
    pass


def free_port() -> int:
    """Ask the OS for a free port, then close it. Small race, acceptable for tests."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Node:
    """A running archipelago process."""

    proc: subprocess.Popen
    base_url: str
    port: int
    data_dir: Path
    log_path: Path
    _stopped: bool = field(default=False, repr=False)

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""

    def alive(self) -> bool:
        return self.proc.poll() is None

    def wait_ready(self, timeout: float = 45.0, interval: float = 0.25) -> None:
        """Poll a TCP predicate until the listener answers, or fail with the log tail."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise NodeFailedToStart(
                    f"node exited with {self.proc.returncode} before becoming ready.\n"
                    f"--- log tail ---\n{self._tail()}"
                )
            if venue_mod.tcp_open("127.0.0.1", self.port, timeout=0.5):
                return
            time.sleep(interval)
        raise NodeFailedToStart(
            f"node not listening on {self.port} after {timeout}s.\n"
            f"--- log tail ---\n{self._tail()}"
        )

    def stop(self, grace: float = 10.0) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)

    def _tail(self, lines: int = 30) -> str:
        return "\n".join(self.log_text().splitlines()[-lines:])


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{Path.home() / '.cargo' / 'bin'}:{env.get('PATH', '')}"
    env.update(extra)
    return env


@contextlib.contextmanager
def start_node(
    *,
    data_dir: Path,
    log_path: Path,
    extra_env: dict[str, str] | None = None,
    port: int | None = None,
    ready_timeout: float = 45.0,
    binary: Path | None = None,
) -> Iterator[Node]:
    """Start the real binary on loopback and yield a Node. Always stops it.

    Deliberately does NOT write to /etc/archipelago/config.toml — the SUT reads its config
    from that hardcoded path (`config.rs:114`) and mutating a system path from a test is
    unacceptable. If the binary cannot be pointed at a private config, the venue raises
    rather than clobbering the host. See docs/BLOCKERS.md.
    """
    binary = binary or sut_mod.build()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    chosen = port or free_port()

    env = _child_env(
        {
            "ARCHY_TEST_ISOLATED": "1",
            **(extra_env or {}),
        }
    )
    log = log_path.open("w")
    proc = subprocess.Popen(
        [str(binary)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=str(data_dir),
        start_new_session=True,
    )
    node = Node(
        proc=proc,
        base_url=f"http://127.0.0.1:{chosen}",
        port=chosen,
        data_dir=data_dir,
        log_path=log_path,
    )
    try:
        node.wait_ready(timeout=ready_timeout)
        yield node
    finally:
        node.stop()
        log.close()


def run_bounded(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command with a hard timeout. Used for SUT scripts that can hang for 900 s."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_child_env(env or {}),
    )
