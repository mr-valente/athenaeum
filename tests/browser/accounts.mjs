import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const base = process.env.TEST_BASE_URL;
assert(base && new URL(base).hostname === 'localhost');
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const problems = [];
  page.on('pageerror', error => problems.push(error.message));
  await page.goto(base + '/');
  await page.getByRole('link', { name: 'Login', exact: true }).click();
  await page.getByRole('heading', { name: 'Account', exact: true }).waitFor();
  assert.equal(await page.locator('body').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(16, 21, 22)');
  await page.screenshot({ path: process.env.TEST_ACCOUNT_SCREENSHOT || '/tmp/athenaeum-account-overview.png', fullPage: true });
  await page.getByRole('button', { name: 'Continue as guest' }).click();
  const shared = (await context.cookies()).find(cookie => cookie.name === '__Host-athenaeum_account');
  assert(shared && shared.secure && shared.httpOnly && shared.path === '/' && shared.sameSite === 'Lax');
  await page.goto(base + '/bernoulli/');
  await page.getByRole('link', { name: 'Guest', exact: true }).waitFor();
  await page.goto(base + '/quacktuaries/');
  await page.getByRole('link', { name: 'Guest', exact: true }).waitFor();
  await page.goto(base + '/account/');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  assert(!(await context.cookies()).some(cookie => cookie.name === '__Host-athenaeum_account'));
  await page.goto(base + '/bernoulli/');
  await page.getByRole('link', { name: 'Login', exact: true }).waitFor();
  assert.deepEqual(problems, []);
  console.log('PASS: public Login link, styled overview, cross-app guest identity, secure cookie and shared sign-out over HTTPS.');
} finally {
  await browser.close();
}
