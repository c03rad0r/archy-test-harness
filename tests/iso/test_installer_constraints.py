"""Constraints the installer imposes on whoever tests it.

Each of these was discovered by booting the image, not by reading it, and each one cost a
wasted installation run. They live here so the next person does not pay for them again.

The distinction matters: these are not bugs in Archipelago. They are the shape of the
thing, and a harness that ignores them reports "install failed" when the truth is "your
virtual disk was the wrong size".

Needs the Archipelago checkout, not an ISO:

    export ARCHY_SUT=~/repos/archy
    pytest tests/iso/test_installer_constraints.py -m venue_any
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from archy_harness import sut

pytestmark = [pytest.mark.venue_any]

BUILDER = "image-recipe/_archived/build-auto-installer-iso.sh"

#: The partitioning step places a partition at 30 GiB. A disk smaller than that yields:
#:     Error: The location 30GiB is outside of the device /dev/sda.
#: 24 GB was observed to fail exactly this way; 40 GB succeeds.
MIN_DISK_GIB = 30


@pytest.fixture(scope="module")
def builder() -> str:
    path = Path(sut.root()) / BUILDER
    if not path.is_file():
        pytest.skip(f"{BUILDER} not present in ARCHY_SUT")
    return path.read_text()


def test_disk_probe_only_recognises_sata_and_nvme(builder: str):
    """A virtio disk is invisible to the installer, so a VM must use SATA.

    The probe scans /dev/nvme*n1 and /dev/sd[a-z] with removable=0. A virtio-blk disk
    appears as /dev/vda, matches neither pattern, and the install aborts with
    "No suitable internal disk found". This is why the VM harness attaches the scratch
    disk as SATA: not a VM quirk, but the installer's actual contract.
    """
    assert "/dev/nvme*n1" in builder, "the NVMe probe pattern changed or vanished"
    assert "/dev/sd[a-z]" in builder, "the SATA/SCSI probe pattern changed or vanished"
    assert "/dev/vd" not in builder, (
        "the installer now probes virtio disks — update the VM harness and this test"
    )


def test_partition_layout_needs_more_than_thirty_gib(builder: str):
    """Pins the 30 GiB floor so a smaller scratch disk fails here, not 10 minutes in."""
    sizes = re.findall(r"mkpart[^\n]*?(\d+)GiB", builder)
    assert sizes, "no GiB-sized mkpart found — the partition layout changed shape"
    largest = max(int(size) for size in sizes)
    assert largest >= MIN_DISK_GIB, (
        f"largest partition is {largest} GiB, expected at least {MIN_DISK_GIB}"
    )


def test_packaged_fips_unit_is_masked_before_the_deb_installs(builder: str):
    """Ordering is the whole point: mask after install, or dpkg re-enables the unit.

    Both `fips.service` (package) and `archipelago-fips.service` (wrapper) run the same
    binary against the same config. `Conflicts=` is symmetric and designates no winner,
    so whichever starts second crash-loops. The mask is what makes Archipelago the sole
    supervisor by construction.
    """
    mask = builder.find("ln -sf /dev/null /etc/systemd/system/fips.service")
    assert mask != -1, "the packaged fips unit is no longer masked in the image"
    install = builder.find("/tmp/fips.deb")
    assert install != -1, "could not find where the fips .deb is installed"
    assert mask > install, (
        "the mask must come after the .deb install — installing the package recreates the "
        "unit and would silently undo a mask applied earlier"
    )


def test_iso_does_not_rely_on_a_serial_console(builder: str):
    """Documents why the VM harness reads the framebuffer and never a serial log.

    The installed kernel command line carries no console=ttyS0, so -serial produces an
    empty file while the installer is happily running. Progress is observable only via
    the QEMU monitor's screendump.
    """
    has_serial = bool(re.search(r"console=ttyS0", builder))
    if has_serial:
        pytest.skip("the image now sets console=ttyS0 — serial capture is viable again")
    assert "grub" in builder.lower(), (
        "no serial console configured and no bootloader config found; "
        "re-examine how the image boots"
    )
