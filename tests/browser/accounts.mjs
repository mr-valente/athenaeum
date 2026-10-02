import { chromium, expect } from 'playwright/test';
import assert from 'node:assert/strict';

const base = process.env.TEST_BASE_URL;
assert(base && new URL(base).hostname === 'localhost');
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const problems = [];
  page.on('pageerror', error => problems.push(error.message));
  const securityProblems = [];
  page.on('console', message => { if (message.type() === 'error') securityProblems.push(message.text()); });
  await page.goto(base + '/');
  await page.getByRole('link', { name: 'Login', exact: true }).click();
  await page.getByRole('heading', { name: 'Account', exact: true }).waitFor();
  assert.equal(await page.locator('body').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(16, 21, 22)');
  await page.screenshot({ path: process.env.TEST_ACCOUNT_SCREENSHOT || '/tmp/athenaeum-account-overview.png', fullPage: true });
  assert.equal(await page.getByRole('button', { name: 'Sign out on every device' }).count(), 0);
  const googleBox = await page.getByRole('button', { name: 'Sign in with Google' }).boundingBox();
  const guestBox = await page.getByRole('button', { name: 'Sign in as guest' }).boundingBox();
  assert(guestBox.y > googleBox.y + googleBox.height);
  await page.getByRole('button', { name: 'Sign in as guest' }).click();
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
  await page.goto(base + '/account/');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.getByText('Sign in to save your activity.', { exact: true }).waitFor();
  assert(!(await context.cookies()).some(cookie => cookie.name === '__Host-athenaeum_account'));
  await page.goto(base + '/bernoulli/');
  await page.getByRole('link', { name: 'Login', exact: true }).waitFor();
  await page.goto(base + '/');
  await page.getByRole('link', { name: 'Login', exact: true }).waitFor();
  // Test-only account fixture already exists in the disposable database.
  await context.addCookies([{ name: '__Host-athenaeum_account', value: 'browser-token',
    url: base + '/', secure: true, httpOnly: true, sameSite: 'Lax' }]);
  await page.goto(base + '/account/');
  await page.getByLabel('Display name').fill('Dr. Rowan');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByLabel('Display name')).toHaveValue('Dr. Rowan');
  const logoutBox = await page.getByRole('button', { name: 'Sign out', exact: true }).boundingBox();
  const signedGoogleBox = await page.getByRole('button', { name: 'Sign in with Google' }).boundingBox();
  assert(logoutBox.y > signedGoogleBox.y + signedGoogleBox.height);
  for (const app of ['bernoulli', 'quacktuaries']) {
    const link = page.getByRole('link', { name: app === 'bernoulli' ? 'Bernoulli' : 'Quacktuaries', exact: true });
    assert.equal(await link.count(), 1);
    assert.equal(await link.getAttribute('href'), '/' + app + '/account');
  }
  for (const app of ['bernoulli', 'quacktuaries']) {
    await page.goto(base + '/' + app + '/join');
    await expect(page.getByLabel('Your Name')).toHaveValue('Dr. Rowan');
    assert(await page.getByLabel('Your Name').evaluate(input => input.readOnly));
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
  console.log('PASS: dynamic Login/Account labels, account navigation, editable names, secure guest identity and shared sign-out over HTTPS.');
} finally {
  await browser.close();
}
