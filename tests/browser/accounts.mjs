import { chromium, expect } from 'playwright/test';
import assert from 'node:assert/strict';

const base = process.env.TEST_BASE_URL;
assert(base && new URL(base).hostname === 'localhost');
const browser = await chromium.launch({ headless: true });
let activePage;
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  activePage = page;
  page.on('requestfailed', request => console.error('Request failed: ' + new URL(request.url()).pathname + ' ' + request.failure().errorText));
  async function signOut() {
    const responsePromise = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/auth/logout' && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    const response = await responsePromise;
    assert.equal(response.status(), 303, 'Sign out failed: ' + await page.locator('body').innerText());
    await page.getByText('Sign in to save your activity.', { exact: true }).waitFor();
  }
  const problems = [];
  page.on('pageerror', error => problems.push(error.message));
  const securityProblems = [];
  page.on('console', message => { if (message.type() === 'error') securityProblems.push(message.text()); });
  await page.goto(base + '/');
  assert.equal(await page.getByRole('link', { name: 'Privacy', exact: true }).count(), 0);
  await page.getByRole('link', { name: 'Login', exact: true }).click();
  await page.getByRole('heading', { name: 'Account', exact: true }).waitFor();
  assert.equal(await page.locator('body').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(16, 21, 22)');
  await page.screenshot({ path: process.env.TEST_ACCOUNT_SCREENSHOT || '/tmp/athenaeum-account-overview.png', fullPage: true });
  assert.equal(await page.getByRole('button', { name: 'Sign out on every device' }).count(), 0);
  const googleBox = await page.getByRole('button', { name: 'Sign in with Google' }).boundingBox();
  const guestBox = await page.getByRole('button', { name: 'Sign in as guest' }).boundingBox();
  assert(guestBox.y > googleBox.y + googleBox.height);
  await page.getByLabel('Display name:').fill('Rowan');
  await page.getByRole('button', { name: 'Sign in as guest' }).click();
  await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor();
  await expect(page.getByLabel('Display name:')).toHaveValue('Rowan');
  assert.equal(await page.getByRole('button', { name: 'Sign in with Google' }).count(), 0);
  assert.equal(await page.getByText('Shown in the apps.', { exact: true }).count(), 0);
  const shared = (await context.cookies()).find(cookie => cookie.name === '__Host-athenaeum_account');
  assert(shared && shared.secure && shared.httpOnly && shared.path === '/' && shared.sameSite === 'Lax');
  await page.goto(base + '/');
  await page.getByRole('link', { name: 'Account', exact: true }).waitFor();
  await page.goto(base + '/bernoulli/');
  await page.getByRole('link', { name: 'Account', exact: true }).waitFor();
  assert.equal(await page.locator('.navbar-links a[href$="/account"]').count(), 1);
  await page.getByRole('link', { name: 'Account', exact: true }).click();
  await page.getByRole('heading', { name: 'Account', exact: true }).waitFor();
  await page.getByRole('link', { name: 'Valente Math account', exact: true }).waitFor();
  await page.goto(base + '/quacktuaries/');
  await page.getByRole('link', { name: 'Account', exact: true }).waitFor();
  assert.equal(await page.locator('.navbar-links a[href$="/account"]').count(), 1);
  for (const app of ['bernoulli', 'quacktuaries']) {
    await page.goto(base + '/' + app + '/');
    await page.getByRole('link', { name: 'Teacher Login', exact: true }).click();
    await page.waitForURL(base + '/' + app + '/admin/dashboard');
    assert.equal(await page.locator('input[name="teacher_name"]').count(), 0);
    await page.goto(base + '/' + app + '/join?code=ABC123');
    await expect(page.getByLabel('Join Code')).toHaveValue('ABC123');
    await page.getByText('Joining as Rowan').waitFor();
    assert.equal(await page.locator('input[name="player_name"]').count(), 0);
  }
  await page.getByRole('link', { name: 'Change name', exact: true }).click();
  await page.getByLabel('Display name:').fill('Robin');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await page.waitForURL(base + '/quacktuaries/join?code=ABC123');
  await page.getByText('Joining as Robin').waitFor();
  await expect(page.getByLabel('Join Code')).toHaveValue('ABC123');
  await page.goto(base + '/bernoulli/join');
  await page.getByText('Joining as Robin').waitFor();
  await page.goto(base + '/account/');
  await signOut();
  assert(!(await context.cookies()).some(cookie => cookie.name === '__Host-athenaeum_account'));
  await page.goto(base + '/bernoulli/');
  await page.getByRole('link', { name: 'Login', exact: true }).waitFor();
  await page.goto(base + '/');
  await page.getByRole('link', { name: 'Login', exact: true }).waitFor();
  // Starting from an app preserves the destination and asks for identity once.
  await page.goto(base + '/bernoulli/join?code=ABC123');
  await page.getByRole('heading', { name: 'Sign in', exact: true }).waitFor();
  await page.setViewportSize({ width: 375, height: 812 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByLabel('Display name:').fill('Another guest');
  await page.getByRole('button', { name: 'Sign in as guest' }).click();
  await page.waitForURL(base + '/bernoulli/join?code=ABC123');
  await expect(page.getByLabel('Join Code')).toHaveValue('ABC123');
  await page.getByText('Joining as Another guest').waitFor();
  await page.goto(base + '/account/');
  await page.getByRole('link', { name: 'Save with a Google account', exact: true }).click();
  await page.getByRole('button', { name: 'Sign in with Google' }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Sign in as guest' }).count(), 0);
  await page.goto(base + '/account/');
  await signOut();
  await page.setViewportSize({ width: 1280, height: 900 });
  // Test-only account fixture already exists in the disposable database.
  await context.addCookies([{ name: '__Host-athenaeum_account', value: 'browser-token',
    url: base + '/', secure: true, httpOnly: true, sameSite: 'Lax' }]);
  await page.goto(base + '/account/');
  await page.getByLabel('Display name').fill('Dr. Rowan');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByLabel('Display name')).toHaveValue('Dr. Rowan');
  assert.equal(await page.getByRole('button', { name: 'Sign in with Google' }).count(), 0);
  for (const [app, name] of [['bernoulli', 'Bernoulli'], ['quacktuaries', 'Quacktuaries'], ['srs', 'SRS']]) {
    const link = page.getByRole('link', { name, exact: true });
    assert.equal(await link.count(), 1);
    assert.equal(await link.getAttribute('href'), '/' + app + '/account');
  }
  for (const app of ['bernoulli', 'quacktuaries']) {
    await page.goto(base + '/' + app + '/join');
    await page.getByText('Joining as Dr. Rowan').waitFor();
    assert.equal(await page.locator('input[name="player_name"]').count(), 0);
  }
  await page.goto(base + '/account/');
  await page.getByLabel('Display name').fill('Browser');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await page.goto(base + '/');
  await page.getByRole('link', { name: 'Account', exact: true }).waitFor();
  const otherTab = await context.newPage();
  await otherTab.goto(base + '/account/');
  await otherTab.getByRole('button', { name: 'Sign out', exact: true }).click();
  await otherTab.getByText('Sign in to save your activity.', { exact: true }).waitFor();
  await page.bringToFront();
  // Headless Chromium doesn't consistently issue a native tab-focus event.
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.getByRole('link', { name: 'Login', exact: true }).waitFor();
  assert.deepEqual(problems, []);
  assert.deepEqual(securityProblems, []);
  console.log('PASS: shared named guest and Google identity, direct teacher dashboards, preserved join codes, mobile login, editable names and shared sign-out over HTTPS.');
} catch (error) {
  const failedUrl = new URL(activePage.url());
  console.error('Browser failure at ' + failedUrl.origin + failedUrl.pathname);
  console.error((await activePage.locator('body').innerText()).slice(0, 1200));
  throw error;
} finally {
  await browser.close();
}
