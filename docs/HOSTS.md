# Hosts

Workloads belong on the Hermes fleet, not on T14. T14 may be the SSH/Ansible
control point.

| Host | Cores | RAM | Free disk | KVM | Tools | Role |
|---|---:|---:|---:|---|---|---|
| x280 | — | 8 GB | 1 TB | — | — | primary node, P3 UAT |
| x240 | 4 | 7.5 GB | 57 GB | no | none | build first, then second node |
| t440 | 4 | 7.5 GB | 78 GB | yes | podman, qemu, node | VM host when load < 4 |
| inr2 | 16–20 | large | 50 GB → 250 GB | ? | ? | shared build/VM host (blocked) |
| T14 | 18 | 30 GB | 547 GB | yes | — | control only |

## x240 — build host, then node

- Idle at the time of writing; passwordless sudo works.
- No `/dev/kvm`, so it cannot run the VM lane.
- Install podman, qemu tools (for qemu-img), xorriso, debootstrap, Rust.
- Build with `CARGO_BUILD_JOBS=2` to stay within 7.5 GB RAM.
- After the ISO is built and copied, x240 can be wiped and installed as the
  second Archipelago node for the FIPS buyer-proxy test.

## t440 — VM host

- Has KVM, qemu, podman and 78 GB free.
- It produced the existing FIPS onboarding video (`docs/ONBOARDING-E2E.md`).
- Load average was ~15 at the time of writing; run one 3 GB VM only when the
  load is low. Do not use it as a build host.

## x280 — primary node

- 1 TB NVMe, 8 GB RAM, currently blank.
- Install the integration ISO (fork main + selected features), not a raw PR
  branch.
- Drive the UI from another machine; the node has only 8 GB.
- Keep the official ISO as a fallback.

## inr2 — shared build host

- 16–20 cores, large RAM, 50 GB disk (may grow to 250 GB).
- Daily reset; persistence is planned.
- Access is blocked: port 22023 accepts TCP but sends no SSH banner; port 22
  answers and rejects all local keys.
- Once fixed: add to `inventory/hosts.yml`, bootstrap with Ansible, cache
  cargo/ISOs/qcow2 on persistent storage.
- 50 GB is enough for a build-only run with cleanup; 250 GB is needed for
  build + VM images + ISO cache.

## MAAS

MAAS is **not** used to install Archipelago: there is no Archipelago image and
the x280 has no BMC. It could provision a stronger Ubuntu build/VM host if the
MAAS host has the capacity; that is optional and not part of this harness.
