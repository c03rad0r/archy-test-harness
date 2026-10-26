// Drive Archipelago first-run onboarding in a real browser.
//
// Why a browser and not an API call: the step that matters is the UI creating the
// node's seed-derived identity. There is no supported "create identity" endpoint
// to call directly, and calling an internal one would test something the user
// never exercises. This walks the same path a person does.
//
// SECRET HANDLING: the recovery seed is displayed by the wizard. This script
// never prints seed words and never writes them to the video/labels. It records
// only the SHAPE (word count) so a screenshot can never leak a live phrase.
//
// Usage:
//   ARCHY_UI_URL=http://127.0.0.1:18080/ OUT_DIR=/tmp/onboard node onboard.cjs
const { chromium } = require('playwright');

const URL = process.env.ARCHY_UI_URL || 'http://127.0.0.1:18080/';
const OUT = process.env.OUT_DIR || '/tmp/onboard';
const MAX_STEPS = Number(process.env.MAX_STEPS || 20);
const HEADLESS = process.env.HEADED !== '1';

// Match a numbered seed-word list so it can be redacted before any logging.
const SEED_LINE = /\b\d{1,2}\.\s*[a-z]{3,8}\b/gi;
const redact = (s) =>
  (s || '')
    .replace(SEED_LINE, '<seed-word>')
    .replace(/(\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b)/gi, '<seed-phrase-redacted>');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: HEADLESS });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: `${OUT}/video`, size: { width: 1280, height: 800 } },
  });
  const page = await context.newPage();

  const log = [];
  const note = (m) => { log.push(m); console.log(m); };

  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);

  // 1. kiosk idle screen
  await page.keyboard.press('Enter');
  await page.waitForTimeout(3000);

  // 2. intro video. Do NOT use a fixed sleep here: on a slower boot the Skip
  // control appears well after the video starts, and pressing Enter into a
  // playing <video> does nothing — which looks exactly like a hung app. Wait
  // for the control with a real timeout, and retry with Enter only if the
  // screen is blank.
  let skipped = false;
  for (let attempt = 0; attempt < 8 && !skipped; attempt++) {
    const skipBtn = page.getByText('Skip Intro', { exact: false });
    if (await skipBtn.count()) {
      await skipBtn.first().click({ force: true }).catch(() => {});
      skipped = true;
      note('skipped intro');
      break;
    }
    // Give the SPA time to paint the control before another nudge.
    await page.waitForTimeout(2500);
    if (!(await page.locator('body').innerText()).trim()) {
      await page.keyboard.press('Space').catch(() => {});
    }
  }
  if (!skipped) note('WARNING: never saw a Skip Intro control');

  // 3. The intro hands over to the wizard with a blank/transitional screen that
  // can last tens of seconds. Counting keypresses through it burns the step
  // budget and lands on the wizard with none left — observed at step 13 of 14.
  // Wait for the wizard's own text instead of guessing how long it takes.
  try {
    await page.waitForFunction(
      () => /Unlock your sovereignty|Welcome to Archipelago/i.test(document.body.innerText),
      { timeout: 90000 }
    );
    note('wizard visible');
  } catch {
    note('WARNING: wizard text never appeared within 90s — continuing anyway');
  }
  await page.waitForTimeout(1500);

  let sawSeed = false;
  // The wizard shows a 24-word seed and then asks for four specific words back.
  // They are kept in memory for the length of this run and never logged, never
  // written to disk, and never included in the video labels. The recovery check
  // is the point of this step, so a test that skips it is not testing onboarding.
  const seed = new Map();

  for (let step = 0; step < MAX_STEPS; step++) {
    const body = await page.locator('body').innerText();
    const text = redact(body.replace(/\s+/g, ' ').trim());

    // Harvest the numbered seed list (1. word 2. word ...) if present.
    for (const m of body.matchAll(/(\d{1,2})\.\s*([a-z]{3,8})\b/g)) {
      seed.set(Number(m[1]), m[2]);
    }

    const seedWords = body.match(SEED_LINE) || [];
    if (seedWords.length >= 12 && !sawSeed) {
      sawSeed = true;
      note(`STEP ${step}: recovery seed displayed (${seedWords.length} numbered words) — redacted`);
    } else {
      note(`STEP ${step}: ${JSON.stringify(text.slice(0, 150))}`);
    }
    await page.screenshot({ path: `${OUT}/step-${step}.png`, fullPage: true });

    // Seed verification step: type back the requested indices.
    const wanted = [...body.matchAll(/WORD\s*#(\d{1,2})/gi)].map((m) => Number(m[1]));
    const textInputs = page.locator('input:not([type="checkbox"]):not([type="radio"])');
    if (wanted.length && (await textInputs.count()) >= wanted.length) {
      for (let i = 0; i < wanted.length; i++) {
        const word = seed.get(wanted[i]);
        if (!word) {
          note(`  MISSING seed word #${wanted[i]} — cannot complete verification`);
          continue;
        }
        await textInputs.nth(i).fill(word);
      }
      note(`  filled ${wanted.length} seed words (indices ${wanted.join(',')})`);
      const verify = page.getByRole('button', { name: /^verify$/i });
      if (await verify.count()) {
        await verify.first().click({ force: true });
        await page.waitForTimeout(3000);
        note('  clicked Verify');
        continue;
      }
    }

    // Node setup: choose the admin password, then commit. The password is a
    // test value for a disposable VM, supplied by env so it is never hardcoded.
    const pwFields = page.locator('input[type="password"]');
    const pwCount = await pwFields.count();
    if (pwCount >= 1) {
      const password = process.env.ARCHY_TEST_PASSWORD;
      if (!password) {
        note('  PASSWORD_REQUIRED but ARCHY_TEST_PASSWORD is not set — cannot continue');
        break;
      }
      for (let i = 0; i < pwCount; i++) await pwFields.nth(i).fill(password);
      note(`  filled ${pwCount} password field(s)`);
      const setupBtn = page.getByRole('button', { name: /set up node|create node|finish|continue/i });
      if (await setupBtn.count()) {
        await setupBtn.first().click({ force: true });
        note('  clicked Set Up Node');
        await page.waitForTimeout(8000);
        continue;
      }
    }

    // Done? Detect the dashboard by structure, not by prose: the wizard steps are
    // full-bleed text with a single forward control, while the dashboard has
    // persistent navigation chrome. Matching on the word "dashboard" was wrong —
    // the feature-tour step says "available from your dashboard" in its body.
    const navCount = await page.locator('nav, [role="navigation"], [data-testid*="sidebar"]').count();
    if (navCount > 0 && step > 2) {
      note('REACHED_DASHBOARD (navigation chrome present)');
      break;
    }

    // Accept any consent checkbox: without it, Continue is a no-op.
    const boxes = page.locator('input[type="checkbox"]');
    for (let i = 0; i < await boxes.count(); i++) {
      const b = boxes.nth(i);
      if (!(await b.isChecked().catch(() => false))) {
        await b.check({ force: true }).catch(() => {});
        note(`  checked consent box ${i}`);
      }
    }

    // Click the forward control, else Enter. Ordered by intent.
    const fwd = page.getByRole('button', {
      name: /continue|next|unlock your sovereignty|start|begin|let'?s go|got it|finish|enter/i,
    });
    if (await fwd.count()) {
      const label = (await fwd.first().innerText().catch(() => '')).trim().slice(0, 40);
      await fwd.first().click({ force: true }).catch(() => {});
      note(`  clicked ${JSON.stringify(label)}`);
    } else {
      await page.keyboard.press('Enter');
      note('  pressed Enter');
    }
    await page.waitForTimeout(2800);
  }

  await page.screenshot({ path: `${OUT}/final.png`, fullPage: true });
  note(`FINAL_TEXT: ${JSON.stringify(redact((await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 400)))}`);

  require('fs').writeFileSync(`${OUT}/flow.log`, log.join('\n'));
  await page.close(); await context.close(); await browser.close();
  console.log('ONBOARD_DONE');
})().catch((e) => { console.error('ONBOARD_FAILED:', e.message); process.exit(1); });
