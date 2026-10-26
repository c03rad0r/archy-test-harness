"""The venue seam.

A *venue* is where the node under test runs. Tests never know which venue they are on.
Borrowed from PRTA (`lib/venue.py:219-338`), where the whole contract is four functions,
not a class hierarchy.

The two lessons that make this cheap:

1. **The contract is env vars, not objects.** A venue exports a connection contract that
   the rest of the harness already reads. Adding a venue is then additive — zero test
   changes. PRTA proved this: "a venue that exports these env vars needs ZERO scenario
   changes" (`docs/labgrid-venue-design.md:88-91`).
2. **A venue that cannot run says so loudly and specifically.** It does not skip, and it
   does not pretend. `VenueUnavailable` carries the exact reason, which the `required`
   gate turns into NOT VERIFIED.

Deliberately NOT copied from PRTA: three overlapping lock families, a venue enum that is
only a defaults axis, and two co-existing venue modules. See docs/PRTA-WHAT-WE-BORROWED.md.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, Mapping


class VenueUnavailable(RuntimeError):
    """A venue cannot be used on this host.

    This is NOT a test skip. The marker gate converts it into NOT VERIFIED for any test
    marked `required`, and reports the reason verbatim. Never catch this and pass.
    """

    def __init__(self, venue: str, reason: str, fix: str = "") -> None:
        self.venue = venue
        self.reason = reason
        self.fix = fix
        super().__init__(f"venue {venue!r} unavailable: {reason}" + (f" — {fix}" if fix else ""))


@dataclasses.dataclass(frozen=True)
class VenueConfig:
    """The connection contract a venue produces.

    Kept as explicit fields rather than a bag of env vars so that a typo is an error
    rather than a silent miss. `to_env()` is the only place the env-var names live.
    """

    kind: str
    #: Base URL of the RPC/HTTP surface, e.g. http://127.0.0.1:5678
    base_url: str
    #: Data directory the node uses (may be a tempdir for ephemeral venues)
    data_dir: Path
    #: Path to the archipelago binary, if the venue runs one
    binary: Path | None = None
    #: Peer listener address, if this venue exposes one
    peer_addr: str | None = None
    #: FIPS DNS address, if this venue has a real FIPS stack
    fips_dns: str | None = None
    #: Scratch space the venue owns and will clean up
    scratch: Path | None = None
    #: Free-form notes surfaced in run.json so a result explains itself later
    notes: tuple[str, ...] = ()

    def to_env(self) -> dict[str, str]:
        """The only authoritative spelling of the connection contract."""
        env = {
            "ARCHY_VENUE": self.kind,
            "ARCHY_BASE_URL": self.base_url,
            "ARCHY_DATA_DIR": str(self.data_dir),
        }
        if self.binary:
            env["ARCHY_BINARY"] = str(self.binary)
        if self.peer_addr:
            env["ARCHY_PEER_ADDR"] = self.peer_addr
        if self.fips_dns:
            env["ARCHY_FIPS_DNS"] = self.fips_dns
        if self.scratch:
            env["ARCHY_SCRATCH"] = str(self.scratch)
        return env


# --------------------------------------------------------------------------------------
# capability probes — each returns (ok, reason, fix)
# --------------------------------------------------------------------------------------


def _probe_nothing() -> tuple[bool, str, str]:
    return True, "", ""


def _probe_binary(sut: Path | None) -> tuple[bool, str, str]:
    if sut is None:
        return False, "ARCHY_SUT is not set", "export ARCHY_SUT=/path/to/archy"
    binary = find_binary(sut)
    if binary is None:
        return (
            False,
            f"no built binary under {sut} (looked for target/*/archipelago)",
            "cd $ARCHY_SUT/core && cargo build -p archipelago --bin archipelago",
        )
    return True, "", ""


def _probe_fips() -> tuple[bool, str, str]:
    """A real FIPS stack exists only if fips0 is up AND the DNS resolver answers."""
    iface = Path("/proc/net/if_inet6")
    if not iface.exists():
        return False, "/proc/net/if_inet6 missing (no IPv6)", "enable IPv6"
    try:
        text = iface.read_text()
    except OSError as exc:  # pragma: no cover - unreadable proc
        return False, f"cannot read /proc/net/if_inet6: {exc}", ""
    if "fips0" not in text:
        return (
            False,
            "no fips0 interface (FIPS daemon not running)",
            "start the FIPS daemon; see SUT docs/ for the mesh setup",
        )
    # The archipelago peer DNS lives at 127.0.0.1:5354 (dial.rs:89).
    if not tcp_open("127.0.0.1", 5354, timeout=1.0):
        return (
            False,
            "fips0 present but FIPS DNS 127.0.0.1:5354 is not answering",
            "start the FIPS DNS resolver",
        )
    return True, "", ""


def _probe_podman() -> tuple[bool, str, str]:
    if shutil.which("podman") is None:
        return False, "podman not on PATH", "install rootless podman"
    try:
        proc = subprocess.run(
            ["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"podman info failed: {exc}", ""
    if proc.returncode != 0:
        return False, f"podman info exited {proc.returncode}", "podman info"
    if proc.stdout.strip().lower() != "true":
        return False, "podman is not running rootless", "configure rootless podman"
    return True, "", ""


def _probe_iso() -> tuple[bool, str, str]:
    """A built installer image to read, and the tool to read it with."""
    raw = os.environ.get("ARCHY_ISO")
    if not raw:
        return (
            False,
            "ARCHY_ISO is not set",
            "export ARCHY_ISO=/path/to/archipelago-installer-*.iso",
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        return False, f"ARCHY_ISO={path} does not exist", "point it at a built installer image"
    if not shutil.which("xorriso"):
        return False, "xorriso is not installed", "apt-get install xorriso"
    return True, "", ""


# --------------------------------------------------------------------------------------
# the venue table — one entry per venue, each with its capabilities and its 4 verbs
# --------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class VenueSpec:
    name: str
    description: str
    #: What a test on this venue can actually assert, honestly stated
    can_assert: tuple[str, ...]
    #: What it cannot, and why
    cannot_assert: tuple[str, ...]
    probe: Any


VENUES: dict[str, VenueSpec] = {
    "inprocess": VenueSpec(
        name="inprocess",
        description="ApiHandler constructed directly on a tempdir; no listener, no wire.",
        can_assert=(
            "deny paths (401/405) on media routes",
            "auth, CORS, health, static surfaces",
            "RPC methods that only touch the data dir",
            "rental-window per-chunk logic with an injected clock",
        ),
        cannot_assert=(
            "listener behaviour: the 1024-permit semaphore, the v6 mirror, the peer path filter",
            "anything requiring a real FIPS dial",
        ),
        probe=_probe_nothing,
    ),
    "localnode": VenueSpec(
        name="localnode",
        description="The real archipelago binary on loopback with a private data dir.",
        can_assert=(
            "wire behaviour of server.rs: real listener, real semaphore, real path filter",
            "the 900 s non-upgraded deadline (with a shortened probe)",
            "purchase protocol against a fake mint",
        ),
        cannot_assert=(
            "the buyer playback proxy (needs a real FIPS dial)",
            "the seller serve route (needs an installed+Running IndeeHub app)",
        ),
        probe=lambda: _probe_binary(sut_path()),
    ),
    "twonode": VenueSpec(
        name="twonode",
        description="Two processes joined over a real FIPS mesh.",
        can_assert=(
            "federation pairing, peer content browse",
            "the buyer proxy against a real seller",
        ),
        cannot_assert=("nothing yet — not implemented",),
        probe=_probe_fips,
    ),
    "hardware": VenueSpec(
        name="hardware",
        description="A real installed Archipelago box (rootless podman + apps).",
        can_assert=(
            "the media path with a real installed IndeeHub app",
            "container lifecycle, real playback",
        ),
        cannot_assert=("nothing yet — not implemented",),
        probe=_probe_podman,
    ),
    "iso": VenueSpec(
        name="iso",
        description=(
            "A built installer image, read as an artefact (ARCHY_ISO). No node is running; "
            "the ISO itself is the thing under test."
        ),
        can_assert=(
            "which packages the image actually ships, per its own dpkg database",
            "the daemon version embedded in the root filesystem",
            "systemd wiring: which unit is masked, which is enabled",
            "the image boots at all (El Torito BIOS + UEFI entries, sha256 vs sidecar)",
        ),
        cannot_assert=(
            "any runtime behaviour: nothing executes on this venue",
            "whether the daemon starts — that needs a booted node and onboarding, "
            "see docs/INSTALLER-CONSTRAINTS.md",
        ),
        probe=_probe_iso,
    ),
}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def sut_path() -> Path | None:
    raw = os.environ.get("ARCHY_SUT")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def find_binary(sut: Path) -> Path | None:
    """Locate a built archipelago binary. Explicit override wins."""
    override = os.environ.get("ARCHY_BINARY")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        return None
    core = sut / "core"
    for rel in ("target/debug/archipelago", "target/release/archipelago"):
        candidate = core / rel
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    # cargo may place it under a triple dir
    target = core / "target"
    if target.is_dir():
        for candidate in sorted(target.glob("*/debug/archipelago")) + sorted(
            target.glob("*/release/archipelago")
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Liveness probe. TCP, never ICMP — the lesson PRTA paid for."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(name: str) -> tuple[bool, str, str]:
    spec = VENUES.get(name)
    if spec is None:
        return False, f"unknown venue {name!r}", ""
    return spec.probe()


def describe_all() -> list[dict[str, Any]]:
    """Machine-readable capability table, used by `scripts/venue.py --probe`."""
    out = []
    for name, spec in VENUES.items():
        ok, reason, fix = probe(name)
        out.append(
            {
                "venue": name,
                "available": ok,
                "reason": reason,
                "fix": fix,
                "description": spec.description,
                "can_assert": list(spec.can_assert),
                "cannot_assert": list(spec.cannot_assert),
            }
        )
    return out


def require(name: str) -> VenueSpec:
    """Assert a venue is usable, or raise VenueUnavailable with the exact reason."""
    spec = VENUES.get(name)
    if spec is None:
        raise VenueUnavailable(name, f"unknown venue {name!r}", "see docs/VENUES.md")
    ok, reason, fix = probe(name)
    if not ok:
        raise VenueUnavailable(name, reason, fix)
    return spec


def env_contract() -> dict[str, str]:
    """The subset of env the harness reads, for stamping into run.json."""
    keys = (
        "ARCHY_VENUE",
        "ARCHY_BASE_URL",
        "ARCHY_DATA_DIR",
        "ARCHY_BINARY",
        "ARCHY_PEER_ADDR",
        "ARCHY_FIPS_DNS",
        "ARCHY_SCRATCH",
        "ARCHY_SUT",
    )
    return {k: v for k in keys if (v := os.environ.get(k))}


def apply(cfg: VenueConfig, *, respect_existing: bool = False) -> Mapping[str, str]:
    """Export a venue's contract into os.environ and return what was set.

    `respect_existing=False` (authoritative venue) overwrites a stale value — a leftover
    ARCHY_BASE_URL from a previous target is how a run silently tests the wrong box.
    `respect_existing=True` only setdefaults, so an operator's explicit choice wins.
    """
    env = cfg.to_env()
    for key, value in env.items():
        if respect_existing:
            os.environ.setdefault(key, value)
        else:
            os.environ[key] = value
    return env
