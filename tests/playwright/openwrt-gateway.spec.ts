import { expect, test, type BrowserContext, type Page } from '@playwright/test'

const TOKEN = process.env.ARCHY_TEST_TOKEN
const GATEWAY_IP = process.env.ARCHY_TEST_GATEWAY_IP ?? '192.168.2.1'
const GATEWAY_PASSWORD = process.env.ARCHY_TEST_GATEWAY_PASSWORD ?? ''

test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

// Several tests stub get-status while the page keeps polling. Without this,
// a poll that is still in flight when the test ends reports
// "route.fetch: Test ended" and fails an otherwise green test.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {})
})

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

  // The Remote Companion intro auto-pops ~7 s after load unless the stored
  // marker matches the node's current APK build. Seed that marker so it
  // cannot appear mid-test and intercept a click (the mesh-radio modal is
  // handled separately by dismissCompanionOverlay).
  let companionMarker = '1'
  try {
    const releaseUrl = new URL(
      '/packages/archipelago-companion.json',
      baseURL ?? 'http://192.168.50.199',
    ).toString()
    const release = await context.request.get(releaseUrl)
    if (release.ok()) {
      const meta = await release.json()
      if (meta?.versionCode) companionMarker = `build:${meta.versionCode}`
      else if (meta?.versionName) companionMarker = `version:${meta.versionName}`
    }
  } catch {
    // No metadata: keep the default; the dismisser still closes the overlay.
  }

  await context.addInitScript((marker: string) => {
    localStorage.setItem('neode-auth', 'true')
    localStorage.setItem('neode_intro_seen', '1')
    localStorage.setItem('neode_onboarding_complete', '1')
    localStorage.setItem('neode_companion_intro_seen', marker)
    localStorage.setItem('kiosk', 'false')
  }, companionMarker)
}

async function dismissCompanionOverlay(page: Page) {
  // Two global overlays can sit above any dashboard route: the companion
  // intro and the mesh-radio detected wizard. Close both, and wait for late
  // arrivals: the mesh modal re-opens after its status poll confirms the
  // device, so a single immediate check races it. Every click is bounded.
  for (let attempt = 0; attempt < 12; attempt++) {
    const companion = page.getByLabel('Close companion modal')
    if (await companion.isVisible().catch(() => false)) {
      await companion.click({ force: true, timeout: 2_000 }).catch(() => {})
    }
    const modalClose = page.locator('[role="dialog"] button[aria-label="Close"]:visible')
    if ((await modalClose.count().catch(() => 0)) > 0) {
      await modalClose
        .first()
        .click({ force: true, timeout: 2_000 })
        .catch(() => {})
    }
    await page.waitForTimeout(250)
    const blocking =
      (await page.locator('[role="dialog"]:visible').count().catch(() => 0)) > 0 ||
      (await companion.isVisible().catch(() => false))
    // Give a late modal two full checks to show up before declaring victory.
    if (!blocking && attempt >= 2) return
  }
}

async function openGatewayPage(page: Page) {
  await page.goto('/dashboard/server/openwrt', { waitUntil: 'domcontentloaded' })
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({ timeout: 60_000 })
  // The installer's LAN scan can time out on a busy venue network. The page
  // exposes Rescan; retry once before treating discovery as broken.
  if (await page.getByText('Operation failed').isVisible().catch(() => false)) {
    await page.getByRole('button', { name: 'Rescan' }).click().catch(() => {})
    await page.waitForTimeout(1_500)
  }
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
  await dismissCompanionOverlay(page)
  await password.fill(GATEWAY_PASSWORD)
  await dismissCompanionOverlay(page)
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(card).toBeVisible({ timeout: 90_000 })
}

async function connectLabGateway(page: Page) {
  // A MAC-confirmed saved router now connects on load. Do not touch the
  // selector in that case; doing so can re-trigger a connect with a stale
  // host value.
  const card = page.getByTestId('tollgate-card')
  if (await card.isVisible().catch(() => false)) return

  const select = page.getByTestId('gateway-select')
  await expect(select).toBeVisible({ timeout: 60_000 })

  // Discovery can fail on a slow installer scan; use the page's Rescan once.
  if ((await select.locator('option').count()) <= 1) {
    await page.getByRole('button', { name: 'Rescan' }).click().catch(() => {})
    await expect
      .poll(async () => select.locator('option').count(), { timeout: 120_000 })
      .toBeGreaterThan(1)
  }

  const options = select.locator('option')
  const count = await options.count()
  let value = ''
  for (let i = 0; i < count; i++) {
    const option = options.nth(i)
    const text = (await option.innerText()).trim()
    if (!text.includes(GATEWAY_IP)) continue
    // Vue sets the DOM value property; getAttribute('value') can be null.
    value = await option.evaluate((el: HTMLOptionElement) => el.value)
    break
  }
  if (!value) {
    for (let i = 0; i < count; i++) {
      const candidate = await options
        .nth(i)
        .evaluate((el: HTMLOptionElement) => el.value)
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

test('first load connects a MAC-confirmed router or offers discovery', async ({ page }) => {
  const prompt = page.getByTestId('select-gateway-prompt')
  const card = page.getByTestId('tollgate-card')
  // A saved router whose MAC still matches connects before the operator acts.
  // Anything else keeps the discovery-only prompt.
  await expect
    .poll(
      async () =>
        (await prompt.isVisible().catch(() => false)) ||
        (await card.isVisible().catch(() => false)),
      { timeout: 90_000, message: 'gateway did not connect or offer discovery' },
    )
    .toBe(true)

  if (await prompt.isVisible().catch(() => false)) {
    await expect(page.getByTestId('wallet-card')).toHaveCount(0)
    await expect(page.getByTestId('upstream-gateways')).toHaveCount(0)
    await connectLabGateway(page)
  }

  await expect(card).toBeVisible()

  const enabled = card.getByTestId('tollgate-enabled')
  // The lab router may be freshly reflashed. In that state this lane can only
  // verify the connect path and the install card; daemon assertions require an
  // installed TollGate.
  if (await enabled.count()) {
    await expect(enabled).toContainText('Enabled')
    await expect(enabled).not.toContainText('Disabled')

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
  } else {
    await expect(card.getByRole('button', { name: 'Install TollGate' })).toBeVisible()
    await expect(card).toContainText('Payout Lightning address')
  }
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
  // On a vanilla router the private/paid SSID groups do not exist yet; the
  // grouping assertions only apply after TollGate has been installed.
  if ((await privateGroup.count()) === 0) {
    test.skip(
      true,
      'router is vanilla: private/paid SSID groups appear only after install',
    )
  }
  await expect(privateGroup).toHaveCount(1)
  await expect(privateGroup).toContainText('radio0')
  await expect(privateGroup).toContainText('radio1')

  const publicGroup = wifi.locator('[data-ssid^="archipelago-tollgate-"]')
  await expect(publicGroup).toHaveCount(1)
  await expect(publicGroup).toContainText('Paid')

  // The upstream default AP's name varies by package version (`!TollGate-*`,
  // `Archipelago-*`); assert the disabled state, not the spelling.
  const legacy = wifi.locator('[data-disabled="true"]')
  await expect(legacy).toHaveCount(0)
  await dismissCompanionOverlay(page)
  await page.getByTestId('show-legacy-wifi').check()
  await expect(legacy).toHaveCount(1)
})

test('upstreams rank TollGates first and hide the rest behind show more', async ({ page }) => {
  const networks = [
    {
      ssid: 'TollGate-cafe',
      bssid: 'aa:00:00:00:00:01',
      signal: -48,
      encryption: 'none',
      is_tollgate: true,
      price_per_step: 1,
      step_size: 22020096,
    },
    {
      ssid: 'TollGate-market',
      bssid: 'aa:00:00:00:00:02',
      signal: -61,
      encryption: 'psk2',
      is_tollgate: true,
    },
    {
      ssid: 'strong-regular',
      bssid: 'aa:00:00:00:00:03',
      signal: -40,
      encryption: 'psk2',
    },
    {
      ssid: 'mid-regular',
      bssid: 'aa:00:00:00:00:04',
      signal: -55,
      encryption: 'none',
    },
    {
      ssid: 'weak-regular',
      bssid: 'aa:00:00:00:00:05',
      signal: -63,
      encryption: 'psk2',
    },
    {
      ssid: 'weakest-regular',
      bssid: 'aa:00:00:00:00:06',
      signal: -80,
      encryption: 'none',
    },
  ]

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })
    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: true,
        version: '0.6.0-r1',
        pinned_version: '0.6.0',
        up_to_date: true,
        daemon_running: true,
        daemon_version: 'TollGate v0.6.0',
        daemon_version_matches: true,
        enabled: true,
        metric: 'bytes',
        step_size_ms: 22020096,
        price_per_step: 1,
        min_steps: 1,
        configured_mints: 5,
        accepted_mints: [],
        nodogsplash_ok: true,
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    if (body?.method === 'openwrt.upstream-scan') return reply({ networks })
    if (body?.method === 'openwrt.upstream-known') {
      return reply({ known: { known_tollgates: [] } })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await dismissCompanionOverlay(page)
  await page.getByTestId('scan-upstreams').click()

  const list = page.getByTestId('upstream-ranked-list')
  await expect(list).toBeVisible()
  const rows = list.getByTestId('upstream-row')
  await expect(rows).toHaveCount(5)

  const ssids = await rows.evaluateAll(elements =>
    elements.map(element => element.getAttribute('data-ssid')),
  )
  expect(ssids.slice(0, 2)).toEqual(['TollGate-cafe', 'TollGate-market'])
  expect(ssids.slice(2)).toEqual(['strong-regular', 'mid-regular', 'weak-regular'])
  await expect(rows.nth(0)).toHaveAttribute('data-tollgate', 'true')
  await expect(rows.nth(2)).toHaveAttribute('data-tollgate', 'false')

  await expect(rows.nth(0).getByTestId('upstream-price')).toContainText(
    'advertised 1 sat / step',
  )
  await expect(rows.nth(1).getByTestId('upstream-price')).toContainText(
    'no price advertised',
  )
  await expect(list.getByTestId('upstream-caveat')).toContainText('self-reported')
  await expect(list).not.toContainText('verified')
  await expect(list).not.toContainText('unverified')
  await expect(rows.nth(0).getByTestId('upstream-connect')).toBeVisible()
  await expect(rows.nth(2).getByTestId('upstream-use-as-uplink')).toBeVisible()

  await expect(page.getByTestId('upstream-show-more')).toContainText('Show 1 more')
  await page.getByTestId('upstream-show-more').click()
  await expect(rows).toHaveCount(6)
  await expect(rows.nth(5)).toHaveAttribute('data-ssid', 'weakest-regular')
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

  // The ranked list may hide a locked AP behind `Show more`.
  const showMore = page.getByTestId('uplink-show-more')
  if (await showMore.count()) {
    await dismissCompanionOverlay(page)
    await showMore.click()
  }

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

test('uplink scan ranks TollGates first and shows three regular APs', async ({ page }) => {
  const networks = [
    {
      ssid: 'TollGate-uplink',
      bssid: 'bb:00:00:00:00:01',
      signal: -50,
      channel: 6,
      encryption: 'none',
      is_tollgate: true,
    },
    {
      ssid: 'regular-a',
      bssid: 'bb:00:00:00:00:02',
      signal: -45,
      channel: 1,
      encryption: 'psk2',
    },
    {
      ssid: 'regular-b',
      bssid: 'bb:00:00:00:00:03',
      signal: -60,
      channel: 11,
      encryption: 'psk2',
    },
    {
      ssid: 'regular-c',
      bssid: 'bb:00:00:00:00:04',
      signal: -65,
      channel: 3,
      encryption: 'none',
    },
    {
      ssid: 'regular-d',
      bssid: 'bb:00:00:00:00:05',
      signal: -75,
      channel: 9,
      encryption: 'psk2',
    },
  ]

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    if (body?.method === 'openwrt.scan-wifi') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result: { networks },
          error: null,
        }),
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await dismissCompanionOverlay(page)
  await page.getByTestId('configure-uplink').click()
  await dismissCompanionOverlay(page)
  await page.getByTestId('scan-uplink').click()

  const rows = page.getByTestId('uplink-network')
  await expect(rows).toHaveCount(4)
  const ssids = await rows.evaluateAll(elements =>
    elements.map(element => element.getAttribute('data-ssid')),
  )
  expect(ssids).toEqual(['TollGate-uplink', 'regular-a', 'regular-b', 'regular-c'])
  await expect(rows.nth(0)).toHaveAttribute('data-tollgate', 'true')

  await expect(page.getByTestId('uplink-show-more')).toContainText('Show 1 more')
  await page.getByTestId('uplink-show-more').click()
  await expect(rows).toHaveCount(5)
  await expect(rows.nth(4)).toHaveAttribute('data-ssid', 'regular-d')
})

test('uplink sends the scanned band and surfaces association failures', async ({ page }) => {
  let captured: any = null
  const networks = [
    {
      ssid: 'hermes-lan',
      bssid: 'bb:00:00:00:00:10',
      signal: -45,
      channel: 36,
      band: '5',
      encryption: 'wpa-psk',
    },
  ]

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (body?.method === 'openwrt.scan-wifi') {
      return reply({ networks })
    }
    if (body?.method === 'openwrt.configure-wan') {
      captured = body.params
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result: null,
          error: {
            code: -32000,
            message:
              'the router could not associate to "hermes-lan" on any radio (radio1, radio0). A radio can host an AP and a STA only on the same channel.',
          },
        }),
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await dismissCompanionOverlay(page)
  await page.getByTestId('configure-uplink').click()
  await dismissCompanionOverlay(page)
  await page.getByTestId('scan-uplink').click()

  await page.getByTestId('uplink-network').first().click()
  await page.getByTestId('uplink-password').fill('secret')
  await page.getByTestId('uplink-next').click()
  await page.getByTestId('uplink-apply').click()

  await expect(page.getByText('could not associate')).toBeVisible({
    timeout: 20_000,
  })
  expect(captured?.band).toBe('5')
  expect(captured?.encryption).toBe('wpa-psk')
})

test('wallet status is explicit and a zero balance cannot be drained', async ({ page }) => {
  await connectLabGateway(page)

  const wallet = page.getByTestId('wallet-card')
  // The wallet card lives inside the admin panel, which only exists after
  // TollGate is installed. A freshly reflashed router has no wallet to show.
  if (!(await wallet.count())) {
    test.skip(true, 'router is vanilla: the wallet card appears after install')
  }
  await expect(wallet).toBeVisible()
  await expect(wallet).toContainText(/Wallet ready|Wallet degraded/)

  const drain = page.getByTestId('wallet-drain')
  await expect(drain).toBeVisible()
  await expect(drain).toContainText('Drain to Archy wallet')
  await expect(drain).toBeDisabled()
})

async function forceGetStatus(
  page: Page,
  mutate: (result: Record<string, any>) => void,
) {
  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    if (body?.method !== 'openwrt.get-status') return route.continue()
    const response = await route.fetch()
    const json = await response.json()
    const result = json?.result ?? {}
    mutate(result)
    await route.fulfill({
      status: response.status(),
      contentType: 'application/json',
      body: JSON.stringify({ ...json, result }),
    })
  })
}

test('installed TollGate offers reapply without reinstalling', async ({ page }) => {
  await connectLabGateway(page)

  const reapply = page.getByTestId('reapply-config')
  if (!(await reapply.count())) {
    test.skip(true, 'router is not in the installed/admin state')
  }
  await expect(reapply).toBeVisible()
  await expect(reapply).toContainText('Reapply config')
  // Deliberately never click: reapply writes the router configuration.
})

test('install is blocked when the router has no internet uplink', async ({ page }) => {
  await forceGetStatus(page, result => {
    result.wan = { ...(result.wan ?? {}), configured: false, internet: false }
    result.tollgate = {
      ...(result.tollgate ?? {}),
      installed: false,
      up_to_date: false,
      daemon_running: false,
      enabled: false,
    }
  })

  await connectLabGateway(page)

  await expect(page.getByTestId('install-blocked-no-wan')).toBeVisible()
  await expect(page.getByTestId('install-tollgate')).toBeDisabled()
})

test('install progress shows the current step and a percentage', async ({ page }) => {
  const progress = {
    provision_id: 'test-progress',
    kind: 'install',
    phase: 'installer',
    percent: 58,
    current: 'Installing TollGate package',
    done: false,
    failed: false,
    steps: [
      { id: 'verify', label: 'Verify router', status: 'done' },
      { id: 'stage', label: 'Prepare installer', status: 'done' },
      {
        id: 'install',
        label: 'Install TollGate package',
        status: 'running',
        detail: 'apk add tollgate-wrt',
      },
      { id: 'portal', label: 'Configure captive portal', status: 'pending' },
    ],
  }

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const method = body?.method
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (method === 'openwrt.installer.identify') {
      // This test exercises the progress panel, not the trust handshake. The
      // router moved to a new address, so its installer known-hosts entry is
      // intentionally absent; skip the modal here.
      return reply({ ok: true, ssh_fingerprint: '' })
    }
    if (method === 'openwrt.provision-progress') {
      return reply({ ok: true, progress })
    }
    if (method === 'openwrt.provision-tollgate') {
      // Hold the request open long enough for the panel to render. This is a
      // mocked call; the router is never touched.
      await new Promise(resolve => setTimeout(resolve, 15_000))
      return reply({ ok: false, error: 'simulated test timeout' })
    }
    if (method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.wan = { ...(result.wan ?? {}), configured: true, internet: true }
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: false,
        up_to_date: false,
        daemon_running: false,
        enabled: false,
        payout_address: result.tollgate?.payout_address || 'test@example.com',
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)

  const payout = page.locator('input[placeholder="you@wallet.example"]')
  if (await payout.count()) {
    const value = await payout.inputValue()
    if (!value) await payout.fill('test@example.com')
  }
  await dismissCompanionOverlay(page)
  await page.getByTestId('install-tollgate').click()

  await expect(page.getByTestId('provision-progress')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('provision-percent')).toHaveText('58%')
  await expect(page.getByTestId('provision-current')).toContainText(
    'Installing TollGate package',
  )
  await expect(page.getByTestId('provision-step-install')).toContainText(
    'Install TollGate package',
  )
})

test('running install is shown next to the button after a reload', async ({ page }) => {
  const progress = {
    provision_id: 'resume-test',
    kind: 'install',
    phase: 'installer',
    percent: 33,
    current: 'Install TollGate package · Installing tollgate-wrt package',
    done: false,
    failed: false,
    steps: [
      { id: 'verify', label: 'Verify router', status: 'done' },
      {
        id: 'install',
        label: 'Install TollGate package',
        status: 'running',
        detail: 'Installing tollgate-wrt package',
      },
      { id: 'portal', label: 'Configure captive portal', status: 'pending' },
    ],
  }

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (body?.method === 'openwrt.provision-progress') {
      return reply({ ok: true, progress })
    }
    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: false,
        up_to_date: false,
        daemon_running: false,
        enabled: false,
      }
      result.wan = { ...(result.wan ?? {}), configured: true, internet: true }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    return route.continue()
  })

  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({ timeout: 60_000 })
  await dismissCompanionOverlay(page)
  await connectLabGateway(page)

  const button = page.getByTestId('install-tollgate')
  await expect(button).toContainText('33%')
  await expect(button).toContainText('Install TollGate package')
  await expect(page.getByTestId('provision-progress')).toBeVisible()
  await expect(page.getByTestId('provision-current')).toContainText(
    'Install TollGate package',
  )
})

test('completed install collapses to a summary and details', async ({ page }) => {
  const progress = {
    provision_id: 'done-test',
    kind: 'install',
    phase: 'done',
    percent: 94,
    current: 'Complete',
    done: true,
    failed: false,
    steps: [
      { id: 'verify', label: 'Verify router', status: 'done' },
      {
        id: 'install',
        label: 'Install TollGate package',
        status: 'pending',
        detail: 'installed; router moved to its management network',
      },
      {
        id: 'brand',
        label: 'Apply branding',
        status: 'skipped',
        detail: 'not reached — the router moved to its management network',
      },
      {
        id: 'archy-network',
        label: 'Apply SSID and network names',
        status: 'done',
      },
    ],
  }

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })
    if (body?.method === 'openwrt.provision-progress') {
      return reply({ ok: true, progress })
    }
    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: false,
        up_to_date: false,
        daemon_running: false,
        enabled: false,
      }
      result.wan = { ...(result.wan ?? {}), configured: true, internet: true }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    return route.continue()
  })

  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({ timeout: 60_000 })
  await dismissCompanionOverlay(page)
  await connectLabGateway(page)

  const panel = page.getByTestId('provision-progress')
  await expect(panel).toContainText('TollGate install complete')
  await expect(panel.getByTestId('provision-step-install')).toHaveCount(0)
  await panel
    .getByTestId('provision-details')
    .evaluate((element: HTMLElement) => element.click())
  await expect(panel.getByTestId('provision-step-install')).toContainText(
    'Archy completed the post-install steps',
  )
  await expect(panel.getByTestId('provision-step-brand')).toContainText(
    'Handed off to Archy',
  )
})

test('daemon startup is shown instead of wallet degraded', async ({ page }) => {
  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })
    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: true,
        up_to_date: true,
        daemon_running: true,
        daemon_version: 'TollGate v0.6.0',
        daemon_version_matches: true,
        daemon_uptime: '85s',
        enabled: true,
        metric: 'milliseconds',
        step_size_ms: 60000,
        price_per_step: 10,
        min_steps: 1,
        configured_mints: 5,
        advertised_mints: [],
        accepted_mints: [],
        mint_url: '',
        nodogsplash_ok: true,
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    if (body?.method === 'openwrt.wallet-info') {
      return reply({
        ok: true,
        degraded: true,
        error: 'the daemon is not advertising any configured mint',
        wallet: { total_balance: 0, mint_count: 0, mint_balances: {} },
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await expect(page.getByTestId('daemon-starting')).toBeVisible()
  await expect(page.getByTestId('wallet-card')).not.toContainText('Wallet degraded')
  await expect(page.getByTestId('mint-summary')).toContainText('Starting —')
  await expect(page.getByTestId('tollgate-updated')).toBeVisible()
})

test('a reload resumes readiness until the mints advertise', async ({ page }) => {
  // The page has already auto-connected in beforeEach. This test exercises the
  // installed-daemon readiness poll; a vanilla router has no daemon to poll,
  // and the status mock cannot manufacture the select path.
  if (!(await page.getByTestId('tollgate-enabled').count())) {
    test.skip(true, 'router is vanilla: readiness polling needs an installed daemon')
  }
  const mints = [
    'https://mint.coinos.io',
    'https://mint.macadamia.cash',
    'https://mint.westernbtc.com',
    'https://kashu.me',
    'https://mint.cubabitcoin.org',
  ]
  let statusCalls = 0

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (body?.method === 'openwrt.get-status') {
      statusCalls += 1
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      // First snapshot is stale (0 of 5); the resumed poll must fetch again
      // and pick up the advertised mints without a manual Refresh.
      const advertised = statusCalls >= 3 ? mints : []
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: true,
        up_to_date: true,
        daemon_running: true,
        daemon_version: 'TollGate v0.6.0',
        daemon_version_matches: true,
        daemon_uptime: '85s',
        enabled: true,
        metric: 'milliseconds',
        step_size_ms: 60000,
        price_per_step: 10,
        min_steps: 1,
        configured_mints: 5,
        advertised_mints: advertised.map(url => ({
          url,
          price: 10,
          unit: 'sat',
          min_steps: 1,
        })),
        accepted_mints: mints.map(url => ({
          url,
          price_per_step: 10,
          price_unit: 'sat',
          purchase_min_steps: 1,
          min_balance: 64,
          min_payout_amount: 128,
          payout_interval_seconds: 60,
          balance_tolerance_percent: 10,
        })),
        mint_url: '',
        nodogsplash_ok: true,
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    if (body?.method === 'openwrt.wallet-info') {
      return reply({
        ok: true,
        degraded: false,
        wallet: { total_balance: 0, mint_count: 0, mint_balances: {} },
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await expect(page.getByTestId('daemon-starting')).toBeVisible()
  await expect(page.getByTestId('mint-summary')).toContainText('Starting —')
  await expect(page.getByTestId('mint-summary')).toContainText('5 of 5', {
    timeout: 20_000,
  })
  await expect(page.getByTestId('daemon-starting')).toHaveCount(0)
  await expect(page.getByTestId('gateway-updated')).toContainText('Updated')
})

// The shapes observed after a vanilla reflash: the saved address is answered
// by an unrelated HTTP-only host reached through the lab gateway, while the
// real router sits on the USB NIC at 192.168.1.1.
const PHANTOM_DISCOVERY = {
  routers: [
    {
      ip: '192.168.2.1',
      mac: '',
      model: 'unknown',
      identified: false,
      ssh_open: false,
      source: 'well-known router address',
      active: true,
      reachable: false,
      on_link: false,
      via: '192.168.50.1',
      interface: 'enx9c69d3c70689',
      link_type: 'ethernet',
      has_saved_credentials: true,
    },
    {
      ip: '192.168.1.1',
      mac: '94:83:c4:8c:59:c3',
      model: 'glinet,gl-mt3000',
      name: 'GL-MT3000',
      firmware: 'OpenWrt 25.12.5',
      identified: true,
      ssh_open: true,
      password_status: 'not_required',
      active: false,
      reachable: true,
      on_link: true,
      via: '',
      interface: 'enx6c6e0750cd79',
      link_type: 'ethernet',
      subnet: '192.168.1.0/24',
      has_saved_credentials: false,
    },
  ],
  active: '192.168.2.1',
  active_address: '192.168.2.1',
  active_reachable: false,
  suggested: '192.168.1.1',
}

const MINIMAL_STATUS = {
  host: '192.168.1.1',
  hostname: 'OpenWrt',
  wan: { configured: false, internet: false, ssid: '', ip: '', radio: '' },
  tollgate: {
    installed: false,
    pinned_version: '0.6.0',
    up_to_date: false,
    version: '',
    version_known: false,
  },
}

async function mockDiscoveryAndStatus(
  page: Page,
  onSelectRouter?: (body: any) => unknown,
) {
  const selectCalls: any[] = []
  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })
    if (body?.method === 'openwrt.discover') return reply(PHANTOM_DISCOVERY)
    if (body?.method === 'openwrt.get-status') return reply(MINIMAL_STATUS)
    if (body?.method === 'openwrt.select-router') {
      selectCalls.push(body?.params)
      if (onSelectRouter) return reply(onSelectRouter(body))
      return reply({
        ok: false,
        host: body?.params?.host,
        needs_password: true,
        needs_root_password: true,
        bad_password: false,
        error:
          'This router has no root password yet. Vanilla OpenWrt accepts any password over Dropbear, but Archy needs a real one.',
      })
    }
    return route.continue()
  })
  return { selectCalls }
}

test('a stale active address never auto-selects an unrelated HTTP host', async ({ page }) => {
  await mockDiscoveryAndStatus(page)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({ timeout: 60_000 })
  await dismissCompanionOverlay(page)

  // The banner names the saved address and the router that was found instead.
  const moved = page.getByTestId('router-moved')
  await expect(moved).toBeVisible()
  await expect(moved).toContainText('192.168.2.1')
  await expect(moved).toContainText('192.168.1.1')

  // Auto-selection must skip the non-SSH host even though it is `active`.
  await expect(page.getByTestId('gateway-select')).toHaveValue('192.168.1.1')

  // Off-link bystanders stay out of the default list.
  const options = page.getByTestId('gateway-select').locator('option')
  await expect(options.filter({ hasText: '192.168.2.1' })).toHaveCount(0)

  // ...and appear, correctly labelled, behind the explicit toggle.
  await page.getByTestId('show-unverified').check()
  const phantom = options.filter({ hasText: '192.168.2.1' })
  await expect(phantom).toHaveCount(1)
  await expect(phantom).toContainText('via 192.168.50.1')

  // A vanilla router is reported by the installer as needing a password, and
  // the card names the command before the operator even clicks Connect.
  await expect(page.getByTestId('no-root-password')).toBeVisible()
  await expect(page.getByTestId('root-password-command')).toContainText(
    'root@192.168.1.1',
  )
})

test('the moved banner connects to the discovered router', async ({ page }) => {
  const { selectCalls } = await mockDiscoveryAndStatus(page)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('router-moved')).toBeVisible({ timeout: 60_000 })
  await dismissCompanionOverlay(page)

  await page.getByTestId('router-moved-connect').click()

  await expect.poll(() => selectCalls.map(call => call?.host)).toEqual([
    '192.168.1.1',
  ])
  // One guidance block, rendered by the card; the form no longer repeats it.
  await expect(page.getByTestId('root-password-guidance')).toHaveCount(0)
  await expect(page.getByTestId('no-root-password')).toBeVisible()
  await expect(page.getByTestId('root-password-command')).toContainText(
    'passwd root',
  )
})

const IDENTITY_DISCOVERY = {
  routers: [
    {
      ip: '192.168.2.1',
      active: true,
      reachable: false,
      on_link: false,
      via: '192.168.50.1',
      ssh_open: false,
      identified: false,
    },
    {
      ip: '192.168.1.1',
      mac: '94:83:c4:8c:59:c3',
      identified: true,
      ssh_open: true,
      password_status: 'accepted',
      active: false,
      reachable: true,
      on_link: true,
      interface: 'enx6c6e0750cd79',
      link_type: 'ethernet',
      has_saved_credentials: true,
      identity_confirmed: true,
      saved_hostname: 'archipelago-2A39',
    },
  ],
  active: '192.168.2.1',
  active_address: '192.168.2.1',
  active_reachable: false,
  suggested: '192.168.1.1',
}

async function mockIdentityDiscovery(
  page: Page,
  discovery: unknown,
  selectResult: unknown,
) {
  const selectCalls: any[] = []
  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })
    if (body?.method === 'openwrt.discover') return reply(discovery)
    if (body?.method === 'openwrt.get-status') return reply(MINIMAL_STATUS)
    if (body?.method === 'openwrt.select-router') {
      selectCalls.push(body?.params)
      return reply(selectResult)
    }
    return route.continue()
  })
  return { selectCalls }
}

test('a MAC-confirmed move connects without a click and hides the banner', async ({ page }) => {
  const { selectCalls } = await mockIdentityDiscovery(page, IDENTITY_DISCOVERY, {
    ok: true,
    host: '192.168.1.1',
    address: '192.168.1.1',
    username: 'root',
    hostname: 'archipelago-2A39',
  })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({
    timeout: 60_000,
  })
  await dismissCompanionOverlay(page)

  await expect.poll(() => selectCalls.map(call => call?.host)).toEqual([
    '192.168.1.1',
  ])
  expect(selectCalls[0]).toMatchObject({
    ssh_password: '',
    password_provided: false,
  })
  await expect(page.getByTestId('router-moved')).toHaveCount(0)
})

test('a not_required router shows one guidance block and never fires a blank connect', async ({
  page,
}) => {
  const discovery = JSON.parse(JSON.stringify(IDENTITY_DISCOVERY))
  discovery.routers[1].password_status = 'not_required'
  const { selectCalls } = await mockIdentityDiscovery(page, discovery, {
    ok: false,
    needs_password: true,
    needs_root_password: true,
    bad_password: false,
  })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('discovered-gateways')).toBeVisible({
    timeout: 60_000,
  })
  await dismissCompanionOverlay(page)

  await expect(page.getByTestId('no-root-password')).toBeVisible()
  // The card renders the paragraph; the form must not repeat it.
  await expect(page.getByTestId('root-password-guidance')).toHaveCount(0)
  await expect(page.getByTestId('root-password-command')).toContainText(
    'passwd root',
  )
  await expect(page.getByTestId('password-input')).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Connect', exact: true }),
  ).toBeDisabled()
  expect(selectCalls).toHaveLength(0)
})

test('typing the password submits it once without clicking Connect', async ({
  page,
}) => {
  const discovery = JSON.parse(JSON.stringify(IDENTITY_DISCOVERY))
  discovery.routers[1].password_status = 'not_required'
  const { selectCalls } = await mockIdentityDiscovery(page, discovery, {
    ok: true,
    host: '192.168.1.1',
    address: '192.168.1.1',
    username: 'root',
    hostname: 'archipelago-2A39',
  })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('password-input')).toBeVisible({
    timeout: 60_000,
  })
  await dismissCompanionOverlay(page)

  await page.getByTestId('password-input').fill('c03rad0r123')
  await expect.poll(() => selectCalls.length, { timeout: 10_000 }).toBe(1)
  expect(selectCalls[0]).toMatchObject({
    host: '192.168.1.1',
    ssh_password: 'c03rad0r123',
    password_provided: true,
  })
})

test('vanilla OpenWrt panel inspects the image and stays gated', async ({ page }) => {
  await forceGetStatus(page, result => {
    result.tollgate = {
      ...(result.tollgate ?? {}),
      installed: true,
      up_to_date: true,
      daemon_running: true,
      daemon_version: 'TollGate v0.6.0',
      daemon_version_matches: true,
      enabled: true,
    }
  })
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
  // Auto-prepare can still be in flight when the board name arrives.
  await expect(modal.getByTestId('flash-preparing')).toHaveCount(0, {
    timeout: 30_000,
  })
  await expect(modal.getByTestId('flash-start')).toBeDisabled()
  await expect(modal.getByTestId('flash-block-reason')).toBeVisible()

  const preferred = modal
    .locator('[data-testid="flash-link"]')
    .filter({ hasText: 'router link' })
  if (await preferred.count()) {
    const text = await preferred.first().innerText()
    if (!text.includes('192.168.1.')) {
      await expect(preferred.first().getByTestId('flash-prepare-link')).toBeVisible()
    }
  }

  const virtual = modal
    .locator('[data-testid="flash-link"]')
    .filter({ hasText: 'virtual' })
  for (let index = 0; index < (await virtual.count()); index++) {
    await expect(virtual.nth(index).getByTestId('flash-prepare-link')).toHaveCount(0)
  }

  await modal.getByTestId('flash-confirmation').fill('glinet_gl-mt3000')
  await modal.getByTestId('flash-ack').check()
  if (await modal.getByTestId('flash-start').isDisabled()) {
    await expect(modal.getByTestId('flash-block-reason')).toContainText('192.168.1')
  }
  // Deliberately never click flash-start in the regression lane.
  await modal.getByText('Close', { exact: true }).click()
  await expect(modal).toHaveCount(0)
})

test('flash modal prepares and removes the post-flash link automatically', async ({ page }) => {
  let prepared = false
  const prepareCalls: string[] = []
  const removeCalls: string[] = []

  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: true,
        up_to_date: true,
        daemon_running: true,
        daemon_version: 'TollGate v0.6.0',
        daemon_version_matches: true,
        enabled: true,
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    if (body?.method === 'openwrt.installer.clean-slate-info') {
      return reply({
        ok: true,
        host: '192.168.2.1',
        version: '25.12.5',
        board: 'glinet_gl-mt3000',
        image_url: 'https://example.invalid/openwrt.bin',
        sha256: 'deadbeef',
        flash_ready: prepared,
        post_flash_route: prepared ? 'enx0' : undefined,
        links: [
          {
            name: 'enx0',
            carrier: true,
            wireless: false,
            physical: true,
            preferred: true,
            ipv4: prepared
              ? ['192.168.2.153/24', '192.168.1.2/24']
              : ['192.168.2.153/24'],
          },
          {
            name: 'wg0',
            carrier: true,
            wireless: false,
            physical: false,
            preferred: false,
            ipv4: ['10.44.0.1/16'],
          },
        ],
      })
    }
    if (body?.method === 'openwrt.installer.prepare-flash-link') {
      prepareCalls.push(String(body?.params?.interface ?? ''))
      prepared = true
      return reply({
        ok: true,
        interface: body?.params?.interface,
        address: '192.168.1.2/24',
        post_flash_route: 'enx0',
        route_ok: true,
      })
    }
    if (body?.method === 'openwrt.installer.remove-flash-link') {
      removeCalls.push(String(body?.params?.interface ?? ''))
      prepared = false
      return reply({
        ok: true,
        interface: body?.params?.interface,
        address: '192.168.1.2/24',
        removed: true,
        post_flash_route: '192.168.50.1',
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await dismissCompanionOverlay(page)
  // The companion overlay can sit above the advanced panel in this lab.
  await page
    .getByTestId('flash-open')
    .evaluate((element: HTMLElement) => element.click())

  const modal = page.getByTestId('flash-modal')
  await expect(modal.getByTestId('flash-board')).toHaveText('glinet_gl-mt3000')
  await expect.poll(() => prepareCalls).toEqual(['enx0'])
  await expect(modal.getByTestId('flash-prepared')).toContainText('enx0')
  await expect(modal.getByTestId('flash-route')).toContainText('enx0')

  await modal.getByTestId('flash-confirmation').fill('glinet_gl-mt3000')
  await modal.getByTestId('flash-ack').check()
  await expect(modal.getByTestId('flash-start')).toBeEnabled()

  await modal
    .getByTestId('flash-remove-link')
    .evaluate((element: HTMLElement) => element.click())
  await expect.poll(() => removeCalls).toEqual(['enx0'])
  await expect(modal.getByTestId('flash-start')).toBeDisabled()
})

test('a conflicting post-flash route keeps the flash button disabled', async ({ page }) => {
  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: true,
        up_to_date: true,
        daemon_running: true,
        daemon_version: 'TollGate v0.6.0',
        daemon_version_matches: true,
        enabled: true,
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    if (body?.method === 'openwrt.installer.clean-slate-info') {
      return reply({
        ok: true,
        host: '192.168.2.1',
        version: '25.12.5',
        board: 'glinet_gl-mt3000',
        image_url: 'https://example.invalid/openwrt.bin',
        sha256: 'deadbeef',
        flash_ready: false,
        post_flash_route: 'enx1',
        links: [
          {
            name: 'enx0',
            carrier: true,
            wireless: false,
            physical: true,
            preferred: true,
            ipv4: ['192.168.1.2/24'],
          },
          {
            name: 'enx1',
            carrier: true,
            wireless: false,
            physical: true,
            preferred: false,
            ipv4: ['192.168.50.166/24'],
          },
        ],
      })
    }
    return route.continue()
  })

  await connectLabGateway(page)
  await dismissCompanionOverlay(page)
  await page
    .getByTestId('flash-open')
    .evaluate((element: HTMLElement) => element.click())

  const modal = page.getByTestId('flash-modal')
  await expect(modal.getByTestId('flash-board')).toHaveText('glinet_gl-mt3000')
  await expect(modal.getByTestId('flash-block-reason')).toContainText(
    'goes via enx1',
  )
  await modal.getByTestId('flash-confirmation').fill('glinet_gl-mt3000')
  await modal.getByTestId('flash-ack').check()
  await expect(modal.getByTestId('flash-start')).toBeDisabled()
})

test('failed flash offers recheck and trust for the new host key', async ({ page }) => {
  let trusted = false
  let capturedNewPassword = ''
  await page.route('**/rpc/v1', async route => {
    let body: any = {}
    try {
      body = route.request().postDataJSON()
    } catch {
      return route.continue()
    }
    const reply = (result: unknown) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: body?.id ?? 1,
          result,
          error: null,
        }),
      })

    if (body?.method === 'openwrt.get-status') {
      const response = await route.fetch()
      const json = await response.json()
      const result = json?.result ?? {}
      result.tollgate = {
        ...(result.tollgate ?? {}),
        installed: true,
        version: '0.6.0-r1',
        pinned_version: '0.6.0',
        up_to_date: true,
        daemon_running: true,
        daemon_version: 'TollGate v0.6.0',
        daemon_version_matches: true,
        enabled: true,
        metric: 'bytes',
        step_size_ms: 22020096,
        price_per_step: 1,
        min_steps: 1,
        configured_mints: 5,
        accepted_mints: [],
        nodogsplash_ok: true,
      }
      return route.fulfill({
        status: response.status(),
        contentType: 'application/json',
        body: JSON.stringify({ ...json, result }),
      })
    }
    if (body?.method === 'openwrt.installer.clean-slate-info') {
      return reply({
        ok: true,
        host: '192.168.1.1',
        version: '25.12.5',
        board: 'glinet_gl-mt3000',
        image_url: 'https://example.invalid/openwrt.bin',
        sha256: 'deadbeef',
        flash_ready: true,
        links: [
          {
            name: 'enx0',
            carrier: true,
            wireless: false,
            ipv4: ['192.168.1.2/24'],
          },
        ],
      })
    }
    if (body?.method === 'openwrt.installer.clean-slate-flash') {
      capturedNewPassword =
        body?.params?.new_password ?? body?.params?.newPassword ?? ''
      return reply({ ok: true, job_id: 'test-flash' })
    }
    if (body?.method === 'openwrt.installer.clean-slate-status') {
      return reply({
        status: 'failed',
        ip: '192.168.1.1',
        error: 'router did not come back within 3m0s',
        steps: [
          { name: 'identity', status: 'done' },
          { name: 'image', status: 'done' },
          { name: 'flash', status: 'failed', detail: 'router did not return' },
        ],
      })
    }
    if (body?.method === 'openwrt.installer.identify') {
      if (!trusted) return reply({ ssh_fingerprint: 'SHA256:new-key' })
      return reply({
        identified: true,
        firmware: 'OpenWrt 25.12.5',
        model: 'glinet,gl-mt3000',
      })
    }
    if (body?.method === 'openwrt.installer.trust') {
      trusted = true
      return reply({ trusted: true })
    }
    return route.continue()
  })

  page.on('dialog', dialog => dialog.accept())
  await connectLabGateway(page)
  await dismissCompanionOverlay(page)
  await page.getByTestId('flash-open').click()

  const modal = page.getByTestId('flash-modal')
  await expect(modal.getByTestId('flash-board')).toHaveText('glinet_gl-mt3000')
  await modal.getByTestId('flash-confirmation').fill('glinet_gl-mt3000')
  await modal.getByTestId('flash-new-password').fill('newpass123')
  await modal.getByTestId('flash-ack').check()
  await dismissCompanionOverlay(page)
  // The global companion overlay can sit above the flash modal in this lab.
  // Invoke the buttons directly so the test exercises the recovery flow, not
  // the unrelated overlay's stacking order.
  await modal.getByTestId('flash-start').evaluate((element: HTMLElement) => element.click())

  await expect(modal.getByTestId('flash-recheck')).toBeVisible({ timeout: 30_000 })
  await modal.getByTestId('flash-recheck').evaluate((element: HTMLElement) => element.click())
  await expect(modal.getByTestId('flash-recovery-fingerprint')).toContainText(
    'SHA256:new-key',
  )
  await modal
    .getByTestId('flash-recovery-trust')
    .evaluate((element: HTMLElement) => element.click())
  await expect(modal.getByTestId('flash-recovery-reconnect')).toContainText(
    'Reconnect at 192.168.1.1',
  )
  expect(capturedNewPassword).toBe('newpass123')
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
