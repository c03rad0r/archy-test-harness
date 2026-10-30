import { expect, test, type BrowserContext, type Page } from '@playwright/test'

const TOKEN = process.env.ARCHY_TEST_TOKEN
const GATEWAY_IP = process.env.ARCHY_TEST_GATEWAY_IP ?? '10.212.49.1'
const GATEWAY_PASSWORD = process.env.ARCHY_TEST_GATEWAY_PASSWORD ?? ''

test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

async function loginWithDeviceToken(context: BrowserContext, baseURL: string | undefined) {
  if (!TOKEN) {
    throw new Error(
      'ARCHY_TEST_TOKEN is required. Use `make playwright-test` or create a token with scripts/archy-playwright-token.sh.',
    )
  }
  const url = new URL('/rpc/v1', baseURL ?? 'http://192.168.50.199').toString()
  const response = await context.request.post(url, {
    data: {
      jsonrpc: '2.0',
      id: 1,
      method: 'auth.login',
      params: { token: TOKEN },
    },
  })
  expect(response.ok(), `auth.login HTTP ${response.status()}`).toBeTruthy()
  const body = await response.json()
  expect(body.error, JSON.stringify(body.error)).toBeNull()

  await context.addInitScript(() => {
    localStorage.setItem('neode-auth', 'true')
    localStorage.setItem('neode_intro_seen', '1')
    localStorage.setItem('neode_onboarding_complete', '1')
    localStorage.setItem('kiosk', 'false')
  })
}

async function dismissCompanionOverlay(page: Page) {
  const close = page.getByLabel('Close companion modal')
  if (await close.count()) {
    await close.click({ force: true }).catch(() => {})
  }
}

async function openGatewayPage(page: Page) {
  await page.goto('/dashboard/server/openwrt', { waitUntil: 'domcontentloaded' })
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({ timeout: 60_000 })
  await dismissCompanionOverlay(page)
}

async function waitForTollgateOrPassword(page: Page) {
  const card = page.getByTestId('tollgate-card')
  const password = page.locator('input[type="password"]').first()
  await expect
    .poll(
      async () =>
        (await card.isVisible().catch(() => false)) ||
        (await password.isVisible().catch(() => false)),
      { timeout: 90_000, message: 'gateway did not connect or ask for a password' },
    )
    .toBe(true)
  if (await card.isVisible().catch(() => false)) return

  if (!GATEWAY_PASSWORD) {
    throw new Error(
      'The selected gateway requires a root password. Set ARCHY_TEST_GATEWAY_PASSWORD.',
    )
  }
  await password.fill(GATEWAY_PASSWORD)
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(card).toBeVisible({ timeout: 90_000 })
}

async function connectLabGateway(page: Page) {
  const select = page.getByTestId('gateway-select')
  await expect(select).toBeVisible({ timeout: 60_000 })

  const options = select.locator('option')
  const count = await options.count()
  let value = ''
  for (let i = 0; i < count; i++) {
    const option = options.nth(i)
    const text = (await option.innerText()).trim()
    if (text.includes(GATEWAY_IP)) {
      value = (await option.getAttribute('value')) ?? ''
      break
    }
  }
  if (!value) {
    for (let i = 0; i < count; i++) {
      const candidate = await options.nth(i).getAttribute('value')
      if (candidate) {
        value = candidate
        break
      }
    }
  }
  expect(value, `no gateway option containing ${GATEWAY_IP}`).toBeTruthy()

  await select.selectOption(value).catch(() => {})
  const connect = page.getByTestId('connect-gateway')
  // Changing the select already starts the connection. Only click when the
  // button is still enabled (the value was already selected and no change
  // event fired).
  if (await connect.count()) {
    if (await connect.isEnabled().catch(() => false)) {
      await connect.click({ timeout: 10_000 }).catch(() => {})
    }
  }
  await waitForTollgateOrPassword(page)
  await dismissCompanionOverlay(page)
}

test.beforeEach(async ({ context, page, baseURL }) => {
  await loginWithDeviceToken(context, baseURL)
  await openGatewayPage(page)
})

test('first load is discovery-only and the status is not the missing UCI mirror', async ({ page }) => {
  await expect(page.getByTestId('select-gateway-prompt')).toBeVisible()
  await expect(page.getByTestId('wallet-card')).toHaveCount(0)
  await expect(page.getByTestId('upstream-gateways')).toHaveCount(0)

  await connectLabGateway(page)

  const card = page.getByTestId('tollgate-card')
  await expect(card.getByTestId('tollgate-enabled')).toContainText('Enabled')
  await expect(card.getByTestId('tollgate-enabled')).not.toContainText('Disabled')

  const summary = card.getByTestId('mint-summary')
  await expect(summary).toContainText(/Advertising \d+ of \d+ mints/)
  await expect(summary).toContainText(/TollGate v|0\.6\.0/)

  // The UCI mirror being absent must never make a healthy daemon look
  // disabled or zero-priced again.
  await expect(card).not.toContainText('0 sat per 0 milliseconds')
  await expect(card).not.toContainText('daemon may need a restart')

  // Catch a regression that keeps the price but reads the wrong metric:
  // a bytes-metered lab router would look like "Time / 367 minutes".
  const expectedMetric = process.env.ARCHY_EXPECT_METRIC
  if (expectedMetric === 'bytes') {
    await expect(card).toContainText('Data')
    await expect(card).toContainText(/\b21 MB\b/)
  } else if (expectedMetric === 'milliseconds') {
    await expect(card).toContainText('Time')
  }

  await expect(card.getByTestId('details-summary')).toBeVisible()
  await card.getByTestId('details-summary').click()
  await expect(card.getByTestId('tollgate-details')).toHaveJSProperty('open', true)
  await expect(card).toContainText('https://mint.')
})

test('WiFi interfaces are grouped by SSID and legacy disabled APs are hidden', async ({ page }) => {
  await connectLabGateway(page)

  const wifi = page.getByTestId('wifi-interfaces')
  await expect(wifi).toBeVisible()
  const groups = wifi.getByTestId('wifi-group')
  const ssids = await groups.evaluateAll(elements =>
    elements.map(element => element.getAttribute('data-ssid') ?? ''),
  )
  expect(new Set(ssids).size, `duplicate SSID rows: ${ssids.join(', ')}`).toBe(ssids.length)

  const privateGroup = wifi.locator('[data-ssid^="noderunner-"]')
  await expect(privateGroup).toHaveCount(1)
  await expect(privateGroup).toContainText('radio0')
  await expect(privateGroup).toContainText('radio1')

  const publicGroup = wifi.locator('[data-ssid^="archipelago-tollgate-"]')
  await expect(publicGroup).toHaveCount(1)
  await expect(publicGroup).toContainText('Paid')

  const legacy = wifi.locator('[data-ssid^="!TollGate-"]')
  await expect(legacy).toHaveCount(0)
  await dismissCompanionOverlay(page)
  await page.getByTestId('show-legacy-wifi').check()
  await expect(legacy).toHaveCount(1)
})

test('unverified TollGates are explained, hidden by default, and grouped by SSID', async ({ page }) => {
  await connectLabGateway(page)

  const known = page.getByTestId('known-tollgates')
  await expect(known).toBeVisible()
  await expect(known.getByTestId('unverified-explainer')).toContainText(/Verified means/)
  await expect(page.getByTestId('show-unverified-upstreams')).not.toBeChecked()

  const emptyState = known.getByText(/No verified TollGates nearby/)
  if (await emptyState.isVisible().catch(() => false)) {
    await expect(emptyState).toBeVisible()
  }

  await dismissCompanionOverlay(page)
  await page.getByTestId('show-unverified-upstreams').check()
  const groups = known.getByTestId('known-tollgate-group')
  let groupCount = 0
  for (let attempt = 0; attempt < 20; attempt++) {
    groupCount = await groups.count()
    if (groupCount > 0) break
    await page.waitForTimeout(250)
  }
  if (groupCount === 0) {
    test.skip(true, 'no known TollGate history on this node')
  }

  const e750 = groups.filter({ hasText: 'TollGate-E750' })
  if (await e750.count()) {
    await expect(e750).toHaveCount(1)
    await expect(e750).toContainText('unverified')
    await expect(e750).toContainText('2 radios')
    await expect(e750).toContainText('02:83:C4:4D:FC:3B')
    await expect(e750).toContainText('94:83:C4:4D:FC:3A')
    await expect(e750).toContainText('No signed price observed')
    await expect(e750.getByRole('button', { name: 'Connect anyway' })).toBeVisible()
  }
})

test('internet uplink exposes password-protected Wi-Fi without changing it', async ({ page }) => {
  await connectLabGateway(page)

  const uplink = page.getByTestId('internet-uplink')
  await expect(uplink).toBeVisible()
  await expect(uplink).toContainText('Internet uplink')

  await dismissCompanionOverlay(page)
  const configure = page.getByTestId('configure-uplink')
  if (await configure.count()) {
    await configure.click()
  }
  const scan = page.getByTestId('scan-uplink')
  await expect(scan).toBeVisible()
  await dismissCompanionOverlay(page)
  await scan.click()

  const network = page.getByTestId('uplink-network').first()
  const none = uplink.getByText('No networks found')
  await expect
    .poll(
      async () =>
        (await network.isVisible().catch(() => false)) ||
        (await none.isVisible().catch(() => false)),
      { timeout: 90_000, message: 'uplink scan did not finish' },
    )
    .toBe(true)

  if (await none.isVisible().catch(() => false)) return

  const locked = page.getByTestId('uplink-network').filter({ hasText: '🔒' }).first()
  if (await locked.count()) {
    await dismissCompanionOverlay(page)
    await locked.click()
    await expect(page.getByTestId('uplink-password')).toBeVisible()
    // Go back to the list without applying or connecting.
    await dismissCompanionOverlay(page)
    await page.getByTestId('uplink-back').click()
  }
})

test('wallet status is explicit and a zero balance cannot be drained', async ({ page }) => {
  await connectLabGateway(page)

  const wallet = page.getByTestId('wallet-card')
  await expect(wallet).toBeVisible()
  await expect(wallet).toContainText(/Wallet ready|Wallet degraded/)

  const drain = page.getByTestId('wallet-drain')
  await expect(drain).toBeVisible()
  await expect(drain).toContainText('Drain to Archy wallet')
  await expect(drain).toBeDisabled()
})

test('vanilla OpenWrt panel inspects the image and stays gated', async ({ page }) => {
  await connectLabGateway(page)

  const advanced = page.getByTestId('advanced-flash')
  await expect(advanced).toBeVisible()
  await dismissCompanionOverlay(page)
  await advanced.getByTestId('flash-open').click()

  const modal = page.getByTestId('flash-modal')
  await expect(modal).toBeVisible()
  await expect(modal.getByTestId('flash-board')).toHaveText(/glinet_gl-mt3000/, {
    timeout: 90_000,
  })
  await expect(modal.getByTestId('flash-start')).toBeDisabled()

  await modal.getByTestId('flash-confirmation').fill('glinet_gl-mt3000')
  await modal.getByTestId('flash-ack').check()
  // Deliberately never click flash-start in the regression lane.
  await modal.getByText('Close', { exact: true }).click()
  await expect(modal).toHaveCount(0)
})

test('host key is shown before trust, or already trusted', async ({ page }) => {
  await connectLabGateway(page)

  const trust = page.getByTestId('trust-host-key')
  if (await trust.count()) {
    await expect(trust).toContainText(/SHA256:/)
    await expect(trust).toContainText(/dropbearkey -y -f/)
    await expect(page.getByTestId('trust-host-key-button')).toBeVisible()
  } else {
    await expect(page.getByTestId('discovered-gateways')).toContainText(
      /Host key trusted/,
    )
  }
})
