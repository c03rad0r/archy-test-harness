# Onboarding E2E — installing, onboarding, and proving the mesh comes up

The installer lane (`tests/iso`, `docs/INSTALLER-CONSTRAINTS.md`) proves what the image
*contains*. It cannot prove the daemon runs, because on a fresh install it deliberately
does not: `archipelago-fips.service` is gated on

```
ConditionPathExists=/var/lib/archipelago/identity/fips_key
```

and **onboarding** creates that key. This document covers the lane that closes the loop.

## The chain, and what each step proves

| Step | Script | Proves |
|---|---|---|
| Install | `scripts/vm-boot-installer.sh` | the ISO installs to a real disk |
| Boot the result | `scripts/vm-boot-installed.sh` | the installed system boots and serves its UI |
| Shell access | `scripts/inject-ssh-key.sh`, `scripts/enable-ssh-root.sh` | a harness can inspect the guest without a console |
| Onboarding | `tests/onboarding/onboard.cjs` | a real browser completes the wizard and creates the identity |
| Verify | `scripts/verify-fips-onboarding.sh` | the daemon is up and holds an authenticated peer |
| Orchestrate | `scripts/run-onboarding-e2e.sh` | all of the above in one command |

Measured on `archipelago-installer-1.9.1-alpha-unbundled` (fips 0.5.3-1), KVM on t440,
2026-10-26: onboarding produced

```
identity key    /var/lib/archipelago/identity/fips_key   64 bytes, mode 600
supervisor      archipelago-fips = active,  fips.service = masked
daemon          fips 0.5.3 (rev f4a5b4a8e6)
peer            test-us02  connected  parent  etx 1.0  srtt 202 ms
```

## Why the browser, and not an API

Onboarding is a React SPA. There is no supported "create identity" endpoint; the identity
is derived in the UI flow. Calling an internal endpoint would test a path no user
exercises, and would keep passing after the UI broke. Playwright walks the same steps a
person does:

1. kiosk idle screen — press **Enter**
2. intro video — click **Skip Intro**
3. *Welcome Noderunner* → **Unlock your sovereignty**
4. feature tour → **Continue**
5. **24-word recovery seed** → tick consent, **Continue**
6. **Verify Your Seed** — re-enter four requested words
7. `did:key` issued → **Continue**
8. name the identity → **Continue**
9. node password → **Set Up Node**
10. dashboard

Two traps found while building the driver, both worth keeping:

- **Do not detect the dashboard by prose.** The feature-tour step says *"available from
  your dashboard"*, so a `dashboard` regex declares success while still on the tour. The
  driver uses structure instead (navigation chrome present).
- **Do not trust HTTP status for routes.** The SPA serves `index.html` for every path, so
  `/setup`, `/onboarding`, `/api/anything` all return **200**. Status codes are meaningless
  here; only rendered DOM and the real RPC endpoint (`POST /rpc/v1`) say anything.

## Seed handling — non-negotiable

The wizard displays a live 24-word recovery seed. The driver:

- harvests the words **in memory only**, to answer the verification step;
- **never logs them**, never writes them to disk, never puts them in the video;
- prints only the shape (`24 numbered words — redacted`).

Keep it that way. A seed in a log is a compromised node, and this VM's identity is
test-only anyway.

## Requirements

### On the VM host
- `/dev/kvm`, `qemu-system-x86_64`, `qemu-nbd`, `socat`
- `sudo -n` for `modprobe nbd` / `qemu-nbd` (key injection and disk inspection)
- SSH from the workstation, key-based

### On the workstation (where the browser runs)
- `node`
- **Playwright, resolvable from the harness directory.** This is the one gap: the harness
  does not vendor Playwright. Satisfy it either way:

  ```bash
  # a) npm-install into the harness (adds package.json noise)
  cd archy-test-harness && npm i -D playwright && npx playwright install chromium

  # b) borrow another checkout's node_modules (what the fleet does)
  ln -sfn ~/repos/physical-router-test-automation/node_modules \
          ~/repos/archy-test-harness/node_modules
  ```
- Chrome, or Playwright's own Chromium. The driver uses `channel: 'chrome'`; on a host
  where the bundled browser cannot install, that is the difference between working and not.
- `ffmpeg` (system, **not** Playwright's stripped build) to convert webm → mp4.

## Running it

```bash
./scripts/run-onboarding-e2e.sh \
  --host c08r4d0r@fd59:2944:3a9e:9de7:3081:b7d7:e25d:e489 \
  --disk /home/c08r4d0r/archy-vm/archy-test.qcow2 \
  --password 'TestNode-2026-archy'
```

Exit code is the verdict. Artifacts land in `results/onboarding-<timestamp>/`:
per-step screenshots, `video/*.webm`, `flow.log` (redacted), `verify.log`.

To watch it: convert the webm with the **system** ffmpeg —
`ffmpeg -i video/*.webm -c:v libx264 -crf 28 -pix_fmt yuv420p out.mp4`.

## Ports

`vm-boot-installed.sh` forwards `127.0.0.1:2222 → 22` and `127.0.0.1:8080 → 80` **on the VM
host only**. The guest is not published to the network — a half-onboarded node must not be
reachable — so the browser reaches it through an SSH tunnel (`-L 18080:127.0.0.1:8080`).

## Poking at the guest afterwards

```bash
ssh -o ProxyCommand="ssh -W %h:%p <vm-host>" -p 2222 root@127.0.0.1
```

Root login is enabled for the injected key only (`PermitRootLogin prohibit-password`) and
only on a disposable test VM. This is a harness affordance, never a deployment posture.

## Known gaps

- Playwright is not vendored (above). Until it is, this lane needs a manual step, and a
  test that needs a manual step is not yet reproducible — say so when reporting results.
- The wizard's step order is discovered by driving it, not declared. If upstream reorders
  the flow, the driver's generic advance/consent/password handling should survive, but the
  seed-verification step assumes `WORD #N` labels.
- No retry logic on the mesh assertion: a peer must appear within the run. On a quiet
  network with no peers configured, `verify-fips-onboarding.sh` will fail on "no connected
  peers" even though the daemon is healthy. Treat that as a distinct outcome, not a flake.
