SHELL := /bin/bash

REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# SUT and artifacts. Paths are evaluated on the machine running the target;
# with HOST=..., the remote make sees the remote HOME.
REF ?=
ARCHY_REF ?= $(if $(REF),$(REF),main)
ARCHY_SUT ?=
ARCHY_ISO ?=
ARCHY_BIN ?=
PROFILE ?= smoke
VENUE ?=
HOST ?=
NODE ?=
NODE_HOST ?=
NODE_PASSWORD ?=
CMD ?=
STICK ?= /dev/sdc
KALI_REPO ?= $(HOME)/kali-live-headless
VM_WORK ?= $(HOME)/archy-vm
VM_DISK ?= $(VM_WORK)/archy-test.qcow2
RUN_ID ?= latest
OUT ?= $(HOME)/archy-handover.md
URL ?=
MINUTES ?= 16
RELEASE ?= 0
JOBS ?=
BUILD_ONLY ?= 0
LOCAL ?= 0
BECOME_FLAG ?=
ANSIBLE ?= ansible-playbook
INVENTORY := $(REPO)/inventory/hosts.yml
PLAYBOOK := $(REPO)/playbooks/host.yml

.PHONY: help lint host-check host-bootstrap build build-release test smoke critical \
        media media-local iso iso-check vm-install vm-run onboarding usb \
        node-check media-soak report handover evidence pr-check remote

help:
	@printf '%s\n' \
	  'Archipelago test harness' \
	  '' \
	  '  make host-check HOST=x240        # read-only host capability report' \
	  '  make host-bootstrap HOST=x240    # install build/VM toolchain (sudo)' \
	  '  make build HOST=x240 RELEASE=1   # build the archipelago binary' \
	  '  make test HOST=x240 PROFILE=smoke' \
	  '  make smoke | critical | media | media-local' \
	  '  make iso HOST=x240 REF=fix/media-stream-deadline' \
	  '  make iso-check HOST=x240 ARCHY_ISO=/path/on/host.iso' \
	  '  make vm-install HOST=t440 ARCHY_ISO=/path/on/host.iso' \
	  '  make vm-run HOST=t440 VM_DISK=/path/on/host.qcow2' \
	  '  make onboarding NODE_HOST=... NODE_PASSWORD=...' \
	  '  make usb ARCHY_ISO=... STICK=/dev/sdc' \
	  '  make node-check NODE=x280' \
	  '  make media-soak NODE=x280 URL=https://... MINUTES=16' \
	  '  make report RUN_ID=<id> | handover | evidence' \
	  '  make pr-check REF=<branch>       # smoke+critical+media(+iso)' \
	  '  make lint                        # shellcheck/yamllint/ansible/python' \
	  '' \
	  'HOST= runs the target over SSH via scripts/remote.sh. Empty HOST' \
	  'runs locally; do not use T14 as a workload host.'

lint:
	@bash -n $(REPO)/scripts/*.sh
	@shellcheck -S warning $(REPO)/scripts/*.sh
	@yamllint -c $(REPO)/.yamllint.yml $(REPO)/inventory $(REPO)/playbooks $(REPO)/roles
	@$(ANSIBLE) -i $(INVENTORY) $(PLAYBOOK) --syntax-check
	@python3 -m py_compile $(REPO)/scripts/*.py

host-check:
	@if [ -n "$(HOST)" ]; then \
	  $(ANSIBLE) -i $(INVENTORY) -l "$(HOST)" $(PLAYBOOK) --tags check; \
	else \
	  $(REPO)/scripts/build-host-check.sh; \
	fi

host-bootstrap:
	@test -n "$(HOST)" || { echo "usage: make host-bootstrap HOST=x240" >&2; exit 1; }
	@$(ANSIBLE) -i $(INVENTORY) -l "$(HOST)" $(PLAYBOOK) --tags install $(BECOME_FLAG)

build:
	@if [ -n "$(HOST)" ]; then \
	  $(REPO)/scripts/remote.sh "$(HOST)" "make LOCAL=1 build RELEASE=$(RELEASE) JOBS=$(JOBS) ARCHY_REF=$(ARCHY_REF)"; \
	else \
	  $(if $(JOBS),CARGO_BUILD_JOBS=$(JOBS),) \
	  $(REPO)/scripts/build-sut.sh --ref "$(ARCHY_REF)" $(if $(filter 1,$(RELEASE)),--release); \
	fi

build-release:
	@$(MAKE) build RELEASE=1 ARCHY_REF="$(ARCHY_REF)" HOST="$(HOST)"

test:
	@if [ -n "$(HOST)" ]; then \
	  $(REPO)/scripts/remote.sh "$(HOST)" "make LOCAL=1 test PROFILE=$(PROFILE) $(if $(VENUE),VENUE=$(VENUE))"; \
	else \
	  set -a; [ -f "$$HOME/.cache/archy-test-harness/sut.env" ] && . "$$HOME/.cache/archy-test-harness/sut.env"; set +a; \
	  PY="$$HOME/.venvs/archy-harness/bin/python"; [ -x "$$PY" ] || PY=python3; \
	  ARCHY_ISO="$(ARCHY_ISO)" $(if $(ARCHY_BIN),ARCHY_BINARY="$(ARCHY_BIN)",) \
	    "$$PY" "$(REPO)/scripts/run-profile.py" --profile "$(PROFILE)" $(if $(VENUE),--venue "$(VENUE)"); \
	fi

smoke:
	@$(MAKE) test PROFILE=smoke HOST="$(HOST)"

critical:
	@$(MAKE) test PROFILE=critical HOST="$(HOST)"

media:
	@$(MAKE) test PROFILE=media HOST="$(HOST)"

media-local:
	@$(MAKE) test PROFILE=media VENUE=localnode HOST="$(HOST)"

iso:
	@if [ -n "$(HOST)" ]; then \
	  $(REPO)/scripts/remote.sh "$(HOST)" "make LOCAL=1 iso REF=$(ARCHY_REF) BUILD_ONLY=$(BUILD_ONLY)"; \
	else \
	  $(REPO)/scripts/build-iso-on-host.sh \
	    --repo "$${ARCHY_SUT:-$$HOME/archy-fork}" \
	    --ref "$(ARCHY_REF)" \
	    --workdir "$${ARCHY_BUILD:-$$HOME/archy-build}" \
	    $(if $(filter 1,$(BUILD_ONLY)),--build-only); \
	fi

iso-check:
	@$(MAKE) test PROFILE=iso ARCHY_ISO="$(ARCHY_ISO)" HOST="$(HOST)"

vm-install:
	@if [ -n "$(HOST)" ]; then \
	  $(REPO)/scripts/remote.sh "$(HOST)" "make LOCAL=1 vm-install ISO=$(ARCHY_ISO) VM_WORK=$(VM_WORK)"; \
	else \
	  $(REPO)/scripts/vm-install-auto.sh --iso "$(ARCHY_ISO)" --workdir "$(VM_WORK)" --disk-gb 40; \
	fi

vm-run:
	@if [ -n "$(HOST)" ]; then \
	  $(REPO)/scripts/remote.sh "$(HOST)" "make LOCAL=1 vm-run VM_DISK=$(VM_DISK)"; \
	else \
	  $(REPO)/scripts/vm-boot-installed.sh --disk "$(VM_DISK)"; \
	fi

onboarding:
	@test -n "$(NODE_HOST)" || { echo "NODE_HOST is required (user@host)" >&2; exit 1; }
	@test -n "$(NODE_PASSWORD)" || { echo "NODE_PASSWORD is required" >&2; exit 1; }
	@$(REPO)/scripts/run-onboarding-e2e.sh \
	  --host "$(NODE_HOST)" --disk "$(VM_DISK)" --password "$(NODE_PASSWORD)"

usb:
	@$(REPO)/scripts/usb-iso.sh --iso "$(ARCHY_ISO)" --stick "$(STICK)" --kali-repo "$(KALI_REPO)"

node-check:
	@test -n "$(NODE)" || { echo "usage: make node-check NODE=x280" >&2; exit 1; }
	@$(REPO)/scripts/node-check.sh "$(NODE)"

media-soak:
	@test -n "$(URL)" || { echo "usage: make media-soak NODE=x280 URL=https://... MINUTES=16" >&2; exit 1; }
	@$(REPO)/scripts/media-soak.sh --node "$(NODE)" --url "$(URL)" --minutes "$(MINUTES)"

report:
	@$(REPO)/scripts/report.py --run "$(RUN_ID)"

handover:
	@$(REPO)/scripts/handover.py --run "$(RUN_ID)" --out "$(OUT)"

evidence:
	@$(MAKE) report RUN_ID="$(RUN_ID)"
	@$(MAKE) handover RUN_ID="$(RUN_ID)" OUT="$(OUT)"

pr-check:
	@$(REPO)/scripts/pr-check.sh --ref "$(ARCHY_REF)" \
	  $(if $(HOST),--host "$(HOST)") $(if $(ARCHY_ISO),--iso "$(ARCHY_ISO)")

remote:
	@test -n "$(HOST)" || { echo "usage: make remote HOST=x240 CMD='make smoke'" >&2; exit 1; }
	@test -n "$(CMD)" || { echo "CMD is required" >&2; exit 1; }
	@$(REPO)/scripts/remote.sh "$(HOST)" "$(CMD)"
