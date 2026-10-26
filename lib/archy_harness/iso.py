"""Reading the installer ISO as an artifact.

The installer ISO is the one Archipelago deliverable that no unit test can vouch for:
everything interesting about it exists only after a multi-hour container build, inside a
2.6 GB image. Two failure modes have already shipped past "tests are green":

  * a daemon version that was bumped in source but never made it into the image, and
  * a systemd unit masked in a static test but not in the artefact actually flashed.

So these helpers open the ISO that was *built* and read it, rather than trusting the
build log that produced it. The log is the builder's self-report; the image is the fact.

Layout of a built ISO (ARCHIPELAGO 1.9.x, unbundled):

    /archipelago/rootfs.tar          the installed root filesystem, as a tar
    /live/filesystem.squashfs        the live installer environment
    /archipelago/container-images/   on-demand app images (unbundled mode)

Note that a *bundled* build also carries app images here; `unbundled` means the installer
pulls apps from the Marketplace at install time, so absence of an app image is not a
failure — but absence of the daemon is, because the rootfs is always embedded.

Reading requires `xorriso`. It is not a hard requirement of the harness, so a host without
it raises `IsoUnreadable` with the install hint rather than failing obscurely.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

#: Where the installer keeps the root filesystem inside the ISO.
ROOTFS_TAR = "/archipelago/rootfs.tar"


class IsoUnreadable(RuntimeError):
    """The ISO cannot be inspected on this host.

    Distinct from "the ISO is wrong": this is a missing tool, the former is a missing
    package inside a perfectly readable image. Conflating them hides real defects behind
    infrastructure noise, which is the exact failure the `required` gate exists to stop.
    """


def iso_path() -> Path:
    """The ISO under test, from `ARCHY_ISO`."""
    raw = os.environ.get("ARCHY_ISO")
    if not raw:
        raise IsoUnreadable(
            "ARCHY_ISO is not set. Point it at a built installer image:\n"
            "  export ARCHY_ISO=~/worktrees/archy/image-recipe/results/*.iso"
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise IsoUnreadable(f"ARCHY_ISO={path} does not exist")
    return path


def require_xorriso() -> str:
    exe = shutil.which("xorriso")
    if not exe:
        raise IsoUnreadable(
            "xorriso is not installed, so the ISO cannot be read "
            "(Debian/Ubuntu: apt-get install xorriso)"
        )
    return exe


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_iso(path: Path, pattern: str = "") -> list[str]:
    """Every path in the ISO, optionally filtered by `-name` glob."""
    exe = require_xorriso()
    cmd = [exe, "-indev", str(path)]
    cmd += ["-find", "/", "-name", pattern] if pattern else ["-find", "/"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise IsoUnreadable(f"xorriso failed listing {path}: {out.stderr.strip()[:300]}")
    return [line.strip().strip("'") for line in out.stdout.splitlines() if line.strip()]


def extract(path: Path, member: str, dest: Path) -> Path:
    """Extract one member to `dest` (a file path)."""
    exe = require_xorriso()
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [exe, "-osirrox", "on", "-indev", str(path), "-extract", member, str(dest)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if out.returncode != 0 or not dest.exists():
        raise IsoUnreadable(
            f"could not extract {member} from {path}: {out.stderr.strip()[:300]}"
        )
    return dest


def rootfs_tarfile(path: Path) -> tarfile.TarFile:
    """Open the embedded root filesystem as a seekable tar.

    Extracted to a cache on disk rather than streamed, because the dpkg database sits
    ~40 MB into a 2 GB archive and re-reading a stream per member is unusably slow.

    Deliberately NOT `tempfile`: the default temp dir is tmpfs on many hosts, and writing
    2 GB of image into RAM is how a test suite OOM-kills the machine it is testing on.
    The extraction is cached and reused, since a multi-GB copy per test module is slow
    enough that people stop running the suite.

    Override the location with `ARCHY_CACHE`.
    """
    cache = Path(os.environ.get("ARCHY_CACHE", Path.home() / ".cache" / "archy-harness"))
    cache.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    stamp = f"{path.name}-{stat.st_size}-{int(stat.st_mtime)}"
    cached = cache / f"{stamp}.rootfs.tar"
    if not cached.is_file() or cached.stat().st_size == 0:
        tmp = cached.with_suffix(".partial")
        extract(path, ROOTFS_TAR, tmp)
        tmp.replace(cached)
    return tarfile.open(cached, "r")


def read_member(tar: tarfile.TarFile, member: str) -> bytes | None:
    """Read one member, tolerating the leading `./` some tar writers emit."""
    for candidate in (member, f"./{member}"):
        try:
            handle = tar.extractfile(candidate)
        except KeyError:
            continue
        if handle is not None:
            return handle.read()
    return None


def is_symlink(tar: tarfile.TarFile, member: str) -> str | None:
    """The link target if `member` is a symlink, else None."""
    for candidate in (member, f"./{member}"):
        try:
            info = tar.getmember(candidate)
        except KeyError:
            continue
        if info.issym():
            return info.linkname
    return None


def dpkg_record(tar: tarfile.TarFile, package: str) -> dict[str, str] | None:
    """The dpkg status stanza for `package`, as a dict, or None if not installed.

    Reads `var/lib/dpkg/status` — the installed system's own record — rather than
    inferring from the presence of a binary. A binary can be unpacked; only dpkg knows
    whether the package finished configuring.
    """
    raw = read_member(tar, "var/lib/dpkg/status")
    if raw is None:
        return None
    record: dict[str, str] = {}
    found: dict[str, str] | None = None
    for line in io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if not line.strip():
            if found is not None:
                return found
            record = {}
            continue
        if line.startswith(" ") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        record[key.strip()] = value.strip()
        if key.strip() == "Package" and value.strip() == package:
            found = record
    return found
