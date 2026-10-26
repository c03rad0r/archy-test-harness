# What the installer actually requires

Everything here was found by booting a built image, not by reading the builder. Each item
cost at least one failed install run. They are constraints of Archipelago's installer, not
defects to be fixed, so a harness that ignores them reports a false failure.

Tested against `archipelago-installer-1.9.1-alpha-unbundled-x86_64_RC1.iso` (Debian 13,
fips 0.5.3-1).

---

## 1. The scratch disk must be SATA, not virtio

The disk autodetect scans **only**:

```
/dev/nvme*n1     (NVMe)
/dev/sd[a-z]     (SATA/SCSI, removable=0)
```

A virtio-blk disk appears as `/dev/vda`, matches neither pattern, and installation aborts
with `No suitable internal disk found`.

So `scripts/vm-boot-installer.sh` attaches the scratch disk as SATA (`if=ide` on q35). On
a real machine this is invisible; in a VM it is the difference between an install and a
dead end.

## 2. The disk must exceed 30 GiB

`[3/8] Creating partitions` places a partition at 30 GiB. On a smaller disk:

```
Error: The location 30GiB is outside of the device /dev/sda.
```

A 24 GB disk fails; 40 GB succeeds. The resulting layout:

| Partition | Size   | Contents                    |
|-----------|--------|-----------------------------|
| `p1`      | 1 M    | BIOS boot                   |
| `p2`      | 512 M  | EFI (vfat)                  |
| `p3`      | 29.5 G | root (ext4)                 |
| `p4`      | 10 G   | data (**LUKS2**, encrypted) |

Note `p4`: the data partition is encrypted at install time. Root is not, which is why the
installed disk can be inspected offline (see §5).

## 3. There is no serial console

The kernel command line carries no `console=ttyS0`, so `-serial file:...` produces an
**empty file** while the installer is running normally. Anyone watching serial will
conclude the VM is dead when it is fine.

Progress is observable only through the QEMU monitor:

```sh
echo "screendump /path/out.ppm" | socat - UNIX-CONNECT:$HOME/archy-vm/monitor.sock
```

Convert `ppm` → `png` before reading it. Sampling every few minutes is sufficient: it took
roughly 12 minutes from boot to a completed install on a 2 vCPU / 2 GB VM.

## 4. Installation is three prompts, not unattended

Even with the ISO's auto-install payload present, a boot needs three Enter presses:

1. the isolinux menu (`Install Archipelago` is already highlighted);
2. `Found installer at: /run/live/medium` → `Press Enter to install`;
3. `[2/8] Detecting disks` → confirming erasure of the detected disk.

A harness that sends one keypress and waits will sit forever on prompt two.

## 5. The FIPS daemon is dormant until onboarding — by design

After a successful install, `archipelago-fips.service` does **not** start:

```
archipelago-fips.service: skipped, unmet condition check
  ConditionPathExists=/var/lib/archipelago/identity/fips_key
```

`/var/lib/archipelago/identity/` does not exist on a fresh install. The unit documents the
intent:

> Stay dark until onboarding materialises the seed-derived key. Archipelago backend unmasks
> and starts this unit via `sudo systemctl` once the key is present; pre-onboarding the unit
> must be masked so no traffic is sent from an ephemeral identity.

**Consequence for testing:** "FIPS is running right after install" is the wrong assertion.
The correct sequence is install → onboarding (creates the key) → wrapper starts →
`fipsctl show peers` returns data. The dashboard polls `fipsctl show peers` every ~15 s
during this window, which makes it *look* as though a daemon should be up.

## 6. One supervisor, enforced by a mask

Both `fips.service` (from the package) and `archipelago-fips.service` (the wrapper) run the
same binary against the same `/etc/fips/fips.yaml` and bind the same TUN and ports. The
unit files explain the hazard in their own comments: whichever starts second crash-loops on
`address already in use`, leaving a red service indicator while a daemon is in fact running.

`Conflicts=` does not resolve this. It is **symmetric** — it prevents simultaneity but
designates no winner. The image therefore masks the packaged unit:

```sh
ln -sf /dev/null /etc/systemd/system/fips.service
```

and that mask must be applied **after** the `.deb` install, or installing the package
recreates the unit and silently undoes it.

Verified in the shipped artefact, not just in the builder:

- `etc/systemd/system/fips.service` → symlink to `/dev/null`
- `etc/systemd/system/multi-user.target.wants/archipelago-fips.service` → enabled

## 7. Reading the installed system offline

The VM disk can be inspected after shutdown without booting it:

```sh
sudo modprobe nbd max_part=8
sudo qemu-nbd -c /dev/nbd0 --read-only <disk>.qcow2
sudo mount -o ro,noload /dev/nbd0p3 /mnt/vmroot   # noload: skip journal replay
```

`-o ro` alone fails on a disk from an unclean shutdown; `noload` is required. Then
`/mnt/vmroot/var/log/journal/<machine-id>/` holds the **journal of the actual boot** —
the only way to see whether a unit started without logging into the guest.

That is how the `ConditionPathExists` skip in §5 was found.

Clean up with `sudo umount /mnt/vmroot` and `sudo qemu-nbd -d /dev/nbd0`.
