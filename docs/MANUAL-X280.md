# Manual Archy test on the wiped test x280

This runbook is for the **test x280** with the blank 1 TB Samsung 980 PRO.
It is **not** the production fleet gateway x280. Do not run these steps on the
production machine.

Status: official ISO + fork-binary path. The custom ISO is still blocked on the
`apps/` build-context bug.

## Inputs

```text
Installer ISO:
  ~/.cache/ventoy/isos/archipelago-installer-1.9.0-alpha-unbundled-x86_64.iso
  sha256 aad5f0350428976969043079a63b0e7a8264dcee2574526bd34719c798c750af

USB:
  /dev/sdc, 28.7 GB SanDisk (currently the Ubuntu installer)
  Approved for reuse; Ubuntu will be destroyed.

Fork binary:
  ~/archy-bin/archipelago (built on x240 from fix/media-stream-deadline @ 9d50f73)
  Debug build, ~697 MB.

Target disk:
  Samsung SSD 980 PRO 1TB, serial S5GXNF0R724601T
```

## Phase 0 — write the installer USB

On T14, in a terminal that can run sudo:

```bash
cd ~/archy-test-harness
make x280-usb CONFIRM=yes
```

Equivalent manual command:

```bash
ISO=~/.cache/ventoy/isos/archipelago-installer-1.9.0-alpha-unbundled-x86_64.iso
sudo umount /dev/sdc1 /dev/sdc4 2>/dev/null || true
sudo dd if="$ISO" of=/dev/sdc bs=4M status=progress conv=fsync
sync
sudo cmp -n "$(stat -c %s "$ISO")" "$ISO" /dev/sdc && echo "USB verify OK"
```

`make x280-usb` refuses non-USB devices, mounted targets and disks over 100 GB.
It verifies the ISO SHA-256 and the read-back. It destroys `/dev/sdc`.

- [ ] USB written and verified
- [ ] USB safely removed

## Phase 1 — install Archy

1. Connect Ethernet to the test x280 (internet is needed for onboarding/apps).
2. Insert the USB and boot UEFI (F12).
3. Choose **Install Archipelago**.
4. Confirm the three Enter prompts.
5. Select the **1 TB Samsung 980 PRO**, serial `S5GXNF0R724601T`.
   Do not select the USB.
6. Confirm erasure of that disk only.
7. Wait ~12 minutes; reboot; remove the USB.

- [ ] Archy installed on the 1 TB NVMe
- [ ] Node boots without the USB
- [ ] IP address noted

## Phase 2 — onboard with the stock binary

From another machine, open `http://<x280-ip>/`.

1. Set the admin password.
2. **Write down the seed phrase offline.**
3. Finish first-run setup.
4. Install **IndeeHub** from the marketplace.
5. Confirm the stock node is healthy:

```bash
systemctl status archipelago --no-pager
sudo journalctl -u archipelago -n 80 --no-pager
```

- [ ] Onboarding complete
- [ ] Seed backed up offline
- [ ] IndeeHub installed
- [ ] Stock service healthy

## Phase 3 — deploy the fork binary

Once the node has an IP and SSH (or use the console + HTTP transfer):

```bash
cd ~/archy-test-harness
make x280-deploy NODE=archipelago@<x280-ip> BINARY=~/archy-bin/archipelago
```

The helper:

- uploads to `/tmp/archipelago.new`, verifies the SHA-256,
- backs up `/usr/local/bin/archipelago` to `.stock` once,
- installs and restarts `archipelago.service`,
- prints status and the last 100 journal lines.

If SSH is unavailable, serve the binary from T14 and fetch it on the node:

```bash
# T14
cd ~/archy-bin && python3 -m http.server 8000
# x280 console
curl -fL -o /tmp/archipelago.new http://<T14-LAN-IP>:8000/archipelago
sudo cp -a /usr/local/bin/archipelago /usr/local/bin/archipelago.stock
sudo install -m 0755 /tmp/archipelago.new /usr/local/bin/archipelago
sudo systemctl restart archipelago
```

- [ ] Fork binary installed
- [ ] Service active
- [ ] UI reachable
- [ ] No migration/startup errors

If it fails:

```bash
make x280-restore NODE=archipelago@<x280-ip>
```

## Phase 4 — manual tests

### P3 — 900 s media deadline

1. Upload/publish a video longer than 15 minutes in IndeeHub.
2. Play it in the browser and let it run past **15:00**.
3. Expected with the fork: playback continues past 900 s.
   Stock Archy stops at exactly 900 s.
4. Record the screen, elapsed time and `journalctl -u archipelago`.

Generate a test video if needed:

```bash
ffmpeg -f lavfi -i testsrc=size=640x360:rate=10 \
       -f lavfi -i sine=frequency=440 -t 960 \
       -c:v libx264 -preset ultrafast -c:a aac long-16min.mp4
```

- [ ] >15 min video published
- [ ] Played past 15:00
- [ ] Playback continued (fork)
- [ ] Evidence captured

### P1 — media observability

After playback, check bytes served, active viewers and cache hit/miss in the
monitoring UI or `monitoring.media` RPC. These are fork-only additions.

- [ ] Counters visible and non-zero

### P4 — resume/ETag

Interrupt and resume a download/stream; confirm `206`/`304` and no restart from
zero.

- [ ] Resume behaves correctly

### Buyer proxy

Needs a second node over FIPS. Do not wipe x240 without confirmation; a T440 VM
is the safer second node. This is a later test.

- [ ] Second node available (later)
- [ ] Buyer proxy tested (later)

## Phase 5 — evidence

Save:

- binary SHA-256, `systemctl status`,
- journal covering the test,
- screen recording of the long playback,
- P1 counter values,
- handover:

```bash
make node-check NODE=archipelago@<x280-ip>
make media-soak NODE=archipelago@<x280-ip> URL=<long-media-url> MINUTES=16
make handover RUN_ID=<id>
```

## Rollback

```bash
make x280-restore NODE=archipelago@<x280-ip>
```

This restores `/usr/local/bin/archipelago.stock` and restarts the service.

## Checklist summary

- [ ] USB written/verified
- [ ] Archy installed
- [ ] Onboarded, seed backed up
- [ ] IndeeHub installed
- [ ] Fork binary deployed
- [ ] P3 >15 min playback
- [ ] P1 counters
- [ ] P4 resume
- [ ] Evidence collected
- [ ] Rollback path tested
