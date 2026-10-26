"""The installer ISO as a delivered artefact.

Every claim here was, at some point, only true in a build log while false in the image
that would have been flashed:

  * `fips` was bumped to v0.5.3 in source and in the build `ARG`, but the ISO under test
    carried no daemon at all until the build was actually opened and read;
  * `fips.service` was masked by a static test on the builder script, but masking is only
    real if the symlink exists inside the shipped root filesystem.

Run with a built image:

    export ARCHY_ISO=/path/to/archipelago-installer-*.iso
    pytest tests/iso -m venue_iso

These tests read the artefact. They never read the builder's log, because the log is the
builder's self-report and a self-report is not evidence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archy_harness import iso as isohelpers

pytestmark = [pytest.mark.venue_iso]

#: The daemon must be configured by the package, so dpkg must say `install ok installed`.
EXPECTED_STATUS = "install ok installed"

#: Units the image is expected to ship, and which supervisor must win. See
#: docs/INSTALLER-CONSTRAINTS.md for why these two collide at all.
PACKAGED_UNIT = "etc/systemd/system/fips.service"
ARCHY_UNIT = "etc/systemd/system/archipelago-fips.service"
ARCHY_WANTS = "etc/systemd/system/multi-user.target.wants/archipelago-fips.service"

DAEMON_BINARIES = ("usr/bin/fips", "usr/bin/fipsctl")


@pytest.fixture(scope="module")
def image() -> Path:
    try:
        return isohelpers.iso_path()
    except isohelpers.IsoUnreadable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def rootfs(image: Path):
    try:
        isohelpers.require_xorriso()
    except isohelpers.IsoUnreadable as exc:
        pytest.skip(str(exc))
    tar = isohelpers.rootfs_tarfile(image)
    yield tar
    tar.close()


def test_iso_is_a_bootable_el_torito_image(image: Path):
    """An ISO that cannot boot is not an installer, whatever else it contains."""
    isohelpers.require_xorriso()
    out = subprocess.run(
        ["xorriso", "-indev", str(image), "-report_el_torito", "as_mkisofs"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stderr[-400:]
    assert "-boot" in out.stdout, "ISO has no El Torito boot record"
    # The UEFI entry is the `-eltorito-alt-boot` half; a BIOS-only image boots on the
    # test VM and fails on the hardware a user actually has.
    assert "eltorito-alt-boot" in out.stdout, "ISO has no UEFI boot entry"


@pytest.mark.required
def test_fips_package_is_installed(rootfs):
    """The daemon is installed *and configured*, per the shipped dpkg database."""
    record = isohelpers.dpkg_record(rootfs, "fips")
    assert record is not None, "package `fips` is absent from the shipped root filesystem"
    assert record.get("Status") == EXPECTED_STATUS, (
        f"fips is not fully installed: Status={record.get('Status')!r}"
    )
    version = record.get("Version", "")
    assert version, "fips is installed but carries no Version"
    # Debian version form: <upstream>-<revision>, e.g. 0.5.3-1.
    assert version.count("-") >= 1, f"implausible Debian version {version!r}"


@pytest.mark.required
def test_fips_version_matches_the_pinned_tag(rootfs):
    """The version in the image must be the version the build pins.

    `ARCHY_FIPS_VERSION` is set by whoever runs the suite; leaving it unset is a skip
    rather than a pass, because "any version at all" is the bug this test exists to catch.
    """
    import os

    want = os.environ.get("ARCHY_FIPS_VERSION")
    if not want:
        pytest.skip("set ARCHY_FIPS_VERSION (e.g. 0.5.3) to assert the pinned version")
    record = isohelpers.dpkg_record(rootfs, "fips")
    assert record is not None, "package `fips` is absent"
    assert record.get("Version", "").split("-")[0] == want.lstrip("v"), (
        f"shipped fips is {record.get('Version')}, build pins {want}"
    )


def test_daemon_binaries_are_present(rootfs):
    for binary in DAEMON_BINARIES:
        assert isohelpers.read_member(rootfs, binary) is not None, f"{binary} missing"


@pytest.mark.required
def test_packaged_unit_is_masked(rootfs):
    """The package's own unit must be masked inside the image.

    Both units run the same binary against the same config and bind the same TUN and
    ports, so whichever starts second crash-loops. `Conflicts=` does not pick a winner —
    it only prevents simultaneity. Masking is what designates the supervisor.
    """
    target = isohelpers.is_symlink(rootfs, PACKAGED_UNIT)
    assert target == "/dev/null", (
        f"{PACKAGED_UNIT} is not masked (link target {target!r}); both supervisors can race"
    )


@pytest.mark.required
def test_archipelago_unit_is_enabled(rootfs):
    """Masking alone would leave no supervisor at all — the wrapper must be wired in."""
    assert isohelpers.read_member(rootfs, ARCHY_UNIT) is not None, f"{ARCHY_UNIT} missing"
    target = isohelpers.is_symlink(rootfs, ARCHY_WANTS)
    assert target is not None, f"{ARCHY_UNIT} is not enabled (no {ARCHY_WANTS})"


def test_daemon_stays_dark_until_onboarding(rootfs):
    """The wrapper is gated on the identity key, and that is deliberate.

    Starting before onboarding would advertise an ephemeral identity and put traffic on
    the mesh under a key nobody owns. Asserted as a contract so that removing the gate is
    a failing test rather than a silent policy change.
    """
    unit = isohelpers.read_member(rootfs, ARCHY_UNIT)
    assert unit is not None
    text = unit.decode("utf-8", "replace")
    assert "ConditionPathExists=/var/lib/archipelago/identity/fips_key" in text, (
        "the wrapper no longer waits for the onboarding identity key"
    )


def test_sha256_sidecar_matches_the_image(image: Path):
    """If the build wrote a sidecar, it must describe *this* image."""
    sidecars = list(image.parent.glob("*.sha256"))
    if not sidecars:
        pytest.skip("no .sha256 sidecar next to the image")
    expected = None
    for sidecar in sidecars:
        for line in sidecar.read_text().splitlines():
            digest, _, name = line.partition(" ")
            if Path(name.strip().lstrip("*")).name == image.name:
                expected = digest.strip()
    if expected is None:
        pytest.skip(f"no sidecar line for {image.name}")
    assert isohelpers.sha256(image) == expected, "image does not match its .sha256"
