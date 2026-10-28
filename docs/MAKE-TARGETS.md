# Make targets

`HOST=` runs the target over SSH on a fleet host. An empty `HOST` runs
locally; T14 is a control point, not a workload host.

## Hosts

```bash
make host-check HOST=x240
make host-bootstrap HOST=x240          # sudo; installs the toolchain
make remote HOST=x240 CMD='make smoke' # arbitrary remote command
```

## Build and tests

```bash
make build HOST=x240 ARCHY_REF=fix/media-stream-deadline
make build-release HOST=x240 ARCHY_REF=fix/media-stream-deadline
make test HOST=x240 PROFILE=smoke
make smoke HOST=x240
make critical HOST=x240
make media HOST=x240
make media-local HOST=x240            # real binary, localnode venue
```

`make build` creates a worktree under `~/worktrees/archy-<ref>` and writes
`~/.cache/archy-test-harness/sut.env` with `ARCHY_SUT` and `ARCHY_BINARY`.
`make test` sources that file automatically.

## ISO and VM

```bash
make iso HOST=x240 REF=fix/media-stream-deadline
make iso-check HOST=x240 ARCHY_ISO=/path/on/x240.iso
make vm-install HOST=t440 ARCHY_ISO=/path/on/t440.iso
make vm-run HOST=t440 VM_DISK=/path/on/t440.qcow2
make onboarding NODE_HOST=user@host NODE_PASSWORD='...'
```

`make iso` runs `scripts/build-iso-on-host.sh` on the selected host. The ISO
path in `iso-check` and `vm-install` is on that host, not on the controller.

`vm-install` drives the three installer prompts through the QEMU monitor. It
is best-effort; use `scripts/vm-install-auto.sh --manual` if the timing drifts.

## Physical USB and node

```bash
make usb ARCHY_ISO=/path/to/build.iso STICK=/dev/sdc
make node-check NODE=x280
make media-soak NODE=x280 URL=https://node/content/... MINUTES=16
```

`make usb` copies the ISO onto an **existing** Ventoy stick and adds a menu
alias. Prepare the stick with `kali-live-headless`:

```bash
make -C ~/kali-live-headless ventoy-all USB=/dev/sdc CONFIRM=yes
```

`media-soak` is the manual P3 acceptance: it streams for 16 minutes and
records duration, bytes and HTTP code in `results/`.

## Evidence

```bash
make report RUN_ID=20261027T...
make handover RUN_ID=20261027T... OUT=~/archy-handover.md
make evidence RUN_ID=20261027T...
make pr-check REF=fix/media-stream-deadline HOST=x240
```

## Lint

```bash
make lint
```

Runs shellcheck, yamllint, Ansible syntax check and Python compilation.

## Test x280 manual runbook

```bash
make x280-runbook
make x280-usb CONFIRM=yes
make x280-deploy NODE=archipelago@<ip> BINARY=~/archy-bin/archipelago
make x280-restore NODE=archipelago@<ip>
```

`x280-usb` writes the verified official ISO to `/dev/sdc` and checks the
read-back. It refuses non-USB devices, mounted targets and disks over 100 GB.
See [`MANUAL-X280.md`](MANUAL-X280.md) for the full runbook.
