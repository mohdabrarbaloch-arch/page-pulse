// Browser verification: drives the real dashboard, captures console errors,
// and screenshots desktop + mobile.
const { chromium } = require('/app/node_modules/playwright');

const BASE = 'http://127.0.0.1:8000';
const OUT = '/workspace/facebook-page-analyzer/docs/screenshots';

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const failedRequests = [];

  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();

  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  page.on('pageerror', (err) => errors.push('PAGEERROR: ' + err.message));
  page.on('requestfailed', (req) => {
    failedRequests.push(`${req.url()} :: ${req.failure()?.errorText}`);
  });

  const step = async (name, fn) => {
    try {
      await fn();
      console.log(`  ok   ${name}`);
    } catch (e) {
      console.log(`  FAIL ${name} :: ${e.message}`);
      throw e;
    }
  };

  console.log('== desktop ==');

  // Register a throwaway account so the run is repeatable. Signing in as the
  // seeded user would collide on the second run when the script adds a page
  // that already exists, and a 409 in the console looks like a real failure.
  const runEmail = `verify-${Date.now()}@example.com`;
  const runPassword = 'verify-run-password';
  const reg = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: runEmail, password: runPassword }),
  });
  const { access_token: runToken } = await reg.json();

  // A brand-new account has no pages, so the dashboard would show its empty
  // state. Add one sample page through the API so the run exercises the real
  // dashboard rather than the onboarding screen.
  await fetch(`${BASE}/api/pages/demo`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${runToken}`,
    },
    body: JSON.stringify({ slug: 'robo-pk', days: 180 }),
  });

  await step('load auth screen', async () => {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForSelector('#auth-screen:not(.is-hidden)', { timeout: 8000 });
  });

  await step('sign in', async () => {
    await page.fill('#login-form input[name="email"]', runEmail);
    await page.fill('#login-form input[name="password"]', runPassword);
    await page.click('#login-form button[type="submit"]');
    await page.waitForSelector('#dashboard:not(.is-hidden)', { timeout: 15000 });
  });

  await step('overview rendered', async () => {
    const name = await page.textContent('#page-name');
    if (!name || !name.trim()) throw new Error('page name empty');
    const followers = await page.textContent('#stat-followers');
    if (!followers || followers === '—') throw new Error('followers not rendered');
    console.log(`       page="${name.trim()}" followers=${followers}`);
  });

  await step('metric cards rendered', async () => {
    const count = await page.locator('#metrics .metric').count();
    if (count < 4) throw new Error(`expected 4 metric cards, got ${count}`);
    const first = await page.textContent('#metrics .metric .metric__value');
    console.log(`       ${count} cards, first value=${first}`);
  });

  await step('insights rendered', async () => {
    const count = await page.locator('#insights .insight').count();
    if (count < 1) throw new Error('no insights rendered');
    const title = await page.textContent('#insights .insight .insight__title');
    console.log(`       ${count} insights, first="${title}"`);
  });

  await step('media mix rendered', async () => {
    const count = await page.locator('#media-mix .mix__row').count();
    if (count < 1) throw new Error('no media mix rows');
    console.log(`       ${count} format rows`);
  });

  await step('time slots rendered', async () => {
    const count = await page.locator('#slots .slot').count();
    if (count < 1) throw new Error('no slots');
    console.log(`       ${count} slots`);
  });

  await step('posts table rendered', async () => {
    const count = await page.locator('#posts-body tr').count();
    if (count < 1) throw new Error('no post rows');
    console.log(`       ${count} post rows`);
  });

  await step('charts rendered', async () => {
    const canvases = await page.locator('canvas').count();
    if (canvases < 2) throw new Error(`expected 2 canvases, got ${canvases}`);
    const chartLoaded = await page.evaluate(() => typeof window.Chart !== 'undefined');
    if (!chartLoaded) throw new Error('Chart.js did not load');
    console.log(`       ${canvases} canvases, Chart.js present`);
  });

  await step('no horizontal overflow at 1440', async () => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    if (overflow > 2) throw new Error(`horizontal overflow of ${overflow}px`);
  });

  await page.screenshot({ path: `${OUT}/01-dashboard-desktop.png`, fullPage: true });
  console.log('  shot 01-dashboard-desktop.png');

  // Window switch
  await step('window switch to 30 days', async () => {
    await page.selectOption('#window-select', '30');
    await page.waitForTimeout(1200);
    const posts = await page.textContent('#stat-posts');
    console.log(`       posts in 30d window = ${posts}`);
  });
  await page.selectOption('#window-select', '180');
  await page.waitForTimeout(1200);

  // Add-page modal
  await step('add-page modal opens', async () => {
    await page.click('#add-page-btn');
    await page.waitForSelector('#add-modal:not(.is-hidden)', { timeout: 5000 });
    // The list is fetched after the modal opens, so wait for it to populate.
    await page.waitForSelector('#demo-list .demo-item', { timeout: 8000 });
    const items = await page.locator('#demo-list .demo-item').count();
    if (items < 1) throw new Error('demo list empty');
    console.log(`       ${items} demo pages listed`);
  });
  await page.screenshot({ path: `${OUT}/02-add-page-modal.png` });
  console.log('  shot 02-add-page-modal.png');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  // Second page
  await step('add second demo page', async () => {
    await page.click('#add-page-btn');
    await page.waitForSelector('#demo-list .demo-item');
    await page.click('#demo-list .demo-item:nth-child(2) button');
    await page.waitForTimeout(2500);
    const tabs = await page.locator('.page-tab').count();
    if (tabs < 2) throw new Error(`expected 2 page tabs, got ${tabs}`);
    console.log(`       ${tabs} page tabs`);
  });
  await page.screenshot({ path: `${OUT}/03-second-page.png`, fullPage: true });
  console.log('  shot 03-second-page.png');

  // Mobile
  console.log('== mobile 390x844 ==');
  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const mpage = await mobile.newPage();
  mpage.on('pageerror', (err) => errors.push('MOBILE PAGEERROR: ' + err.message));

  await step('mobile sign in', async () => {
    await mpage.goto(BASE, { waitUntil: 'networkidle' });
    await mpage.fill('#login-form input[name="email"]', runEmail);
    await mpage.fill('#login-form input[name="password"]', runPassword);
    await mpage.click('#login-form button[type="submit"]');
    await mpage.waitForSelector('#dashboard:not(.is-hidden)', { timeout: 15000 });
  });

  await step('mobile no horizontal overflow', async () => {
    const overflow = await mpage.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    if (overflow > 2) throw new Error(`mobile overflow of ${overflow}px`);
    console.log('       no overflow at 390px');
  });

  await step('mobile metrics stack', async () => {
    const box = await mpage.locator('#metrics .metric').first().boundingBox();
    if (!box || box.width > 200) throw new Error(`metric card too wide: ${box && box.width}`);
    console.log(`       metric card width ${Math.round(box.width)}px`);
  });

  await mpage.screenshot({ path: `${OUT}/04-mobile-dashboard.png`, fullPage: true });
  console.log('  shot 04-mobile-dashboard.png');

  await browser.close();

  console.log('\n== console errors ==');
  if (errors.length) errors.forEach((e) => console.log('  ! ' + e));
  else console.log('  none');

  console.log('== failed requests ==');
  if (failedRequests.length) failedRequests.forEach((r) => console.log('  ! ' + r));
  else console.log('  none');

  if (errors.length || failedRequests.length) process.exit(1);
  console.log('\nALL BROWSER CHECKS PASSED');
})().catch((e) => {
  console.error('VERIFICATION FAILED:', e.message);
  process.exit(1);
});
