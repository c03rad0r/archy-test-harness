import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/playwright',
  outputDir: './results/playwright',
  timeout: 180_000,
  expect: {
    timeout: 20_000,
  },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.ARCHY_BASE_URL ?? 'http://192.168.50.199',
    viewport: { width: 1440, height: 1000 },
    headless: true,
    ignoreHTTPSErrors: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: process.env.ARCHY_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.ARCHY_CHROMIUM_EXECUTABLE }
      : undefined,
  },
})
