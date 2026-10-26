// Playwright config for the TollGate portal evidence run.
//
// Video is recorded for EVERY test, passing or failing, because the artifact's
// job is to let a human confirm the claim without re-running anything.
import { defineConfig } from '@playwright/test';

const PORTAL = process.env.TOLLGATE_PORTAL_URL ?? 'http://localhost:12050';

export default defineConfig({
  testDir: './tests/browser',
  testMatch: '**/tollgate-portal.spec.mjs',
  timeout: 60000,
  retries: 0,
  workers: 1,
  reporter: [
    ['list'],
    ['json', { outputFile: 'results/tollgate/browser/results.json' }],
  ],
  outputDir: 'results/tollgate/browser/artifacts',
  use: {
    baseURL: PORTAL,
    headless: true,
    ignoreHTTPSErrors: true,
    // NoDogSplash redirects every request to its canonical host
    // (192.168.99.1:2050) — the address a customer's browser is expected to
    // reach. A test driving the portal through an SSH tunnel cannot resolve it,
    // so the browser's resolver is told where that host actually is. Transport
    // only: the bytes served, and therefore what is asserted, are the router's.
    launchOptions: {
      args: [
        '--host-resolver-rules=MAP 192.168.99.1 [::1]',
        '--no-sandbox',
      ],
    },
    screenshot: 'on',
    // 'retain-on-failure' would delete the video of a PASSING run — the exact
    // artifact the operator asked for.
    video: 'on',
    actionTimeout: 15000,
    navigationTimeout: 30000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
