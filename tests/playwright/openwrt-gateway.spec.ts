import { expect, test, type BrowserContext, type Page } from '@playwright/test'

const TOKEN = process.env.ARCHY_TEST_TOKEN
const GATEWAY_IP = process.env.ARCHY_TEST_GATEWAY_IP ?? '192.168.2.1'
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

test('failed flash offers recheck and trust for the new host key', async ({ page }) => {
  let trusted = false
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
  await modal.getByTestId('flash-ack').check()
  await modal.getByTestId('flash-start').click()

  await expect(modal.getByTestId('flash-recheck')).toBeVisible({ timeout: 30_000 })
  await modal.getByTestId('flash-recheck').click()
  await expect(modal.getByTestId('flash-recovery-fingerprint')).toContainText(
    'SHA256:new-key',
  )
  await modal.getByTestId('flash-recovery-trust').click()
  await expect(modal.getByTestId('flash-recovery-reconnect')).toContainText(
    'Reconnect at 192.168.1.1',
  )
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
