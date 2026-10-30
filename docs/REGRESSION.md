# Regression lane

The default branch is expected to keep these checks green:

```bash
make lint
make one-engine-check ONE_ENGINE_TARGET=x280_test
make playwright-test PLAYWRIGHT_URL=http://192.168.50.199
```

`make regress` runs all three.

## Playwright

`tests/playwright/openwrt-gateway.spec.ts` is read-only. It authenticates with
a temporary device token created over SSH and removed on exit. It covers:

- discovery-only first load, no admin before selection;
- daemon-sourced status (`Enabled`, metric, step, price), not the old UCI
  mirror;
- compact mint summary and Details;
- grouped WiFi SSIDs and hidden legacy `!TollGate-*` APs;
- verified/unverified upstream grouping;
- Internet uplink scan and password field;
- host-key trust state.

It deliberately does **not** click Connect upstream, Trust, Apply network
names, Drain or Save.

Requirements:

```bash
make playwright-install
export PLAYWRIGHT_URL=http://192.168.50.199
# The runner needs SSH key access to archipelago@192.168.50.199.
```

`make playwright-test` creates the token itself. For a fixed token:

```bash
make playwright-test PLAYWRIGHT_TOKEN=...
```

## Paid-client evidence

A green node UI or a router-local `:2121` response is **not** evidence that a
paid client is gated. From a device connected to the paid SSID:

```bash
./scripts/paid-client-smoke.sh 192.168.99.1
```

It asserts the client can reach the portal and TollGate API, and that ordinary
internet is blocked before payment. Run it after any NoDogSplash or bridge
change.

## CI

- `.github/workflows/harness.yml` runs `make lint` on pushes and pull requests.
- The Playwright job is manual (`workflow_dispatch`) and requires a self-hosted
  runner on the lab network with SSH access to the test node.
- Merging the tests is not enough; the lane must actually be run. Use
  `make regress` before merging or deploying.
