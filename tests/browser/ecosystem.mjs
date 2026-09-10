// Run against ops/local-stack only. Every connection is forced to loopback.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import https from 'node:https';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const base = new URL(process.env.TEST_BASE_URL || 'https://localhost:8443');
assert.equal(base.protocol, 'https:');
assert(/^[a-z0-9.-]+$/.test(base.hostname));
const artifacts = process.env.TEST_ARTIFACTS || path.join(root, '.state/local/checks');
const ca = await readFile(process.env.TEST_CA || path.join(root, '.state/local/edge/data/caddy/pki/authorities/local/root.crt'));
await mkdir(artifacts, { recursive: true, mode: 0o700 });
const savedPath = path.join(artifacts, 'fixture-session.json');
const prefix = '/quacktuaries';

function request(route, { host = base.hostname, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = https.get({ hostname: host, port: base.port || 443, path: route,
      ca, headers, lookup: (_name, options, callback) => options.all
        ? callback(null, [{ address: '127.0.0.1', family: 4 }])
        : callback(null, '127.0.0.1', 4),
    }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers,
        body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
  });
}

// CA validation and real production hostname/SNI are tested here. Chromium's
// isolated context accepts this local CA without installing it on the host.
const health = await request('/_health');
assert.equal(health.status, 200);
const redirect = await request(prefix + '?code=A%2BB&next=x');
assert.equal(redirect.status, 308);
assert.equal(redirect.headers.location, prefix + '/?code=A%2BB&next=x');
const www = await request('/statistics/?test=yes', { host: 'www.' + base.hostname });
assert.equal(www.status, 308);
assert.equal(www.headers.location, base.origin + '/statistics/?test=yes');
for (const route of ['/quacktuaries-other/', '/admin/', prefix + '/_health', '/statistics/example/', '/bernoulli/_health'])
  assert.equal((await request(route)).status, 404, route);
// Bernoulli shares the routing contract: slash redirect, stripped prefix, prefixed links.
const bernoulli = await request('/bernoulli?code=A%2BB');
assert.equal(bernoulli.status, 308);
assert.equal(bernoulli.headers.location, '/bernoulli/?code=A%2BB');
const landing = await request('/bernoulli/');
assert.equal(landing.status, 200);
assert(landing.body.includes('Bernoulli') && landing.body.includes('/bernoulli/join'));
assert.equal((await request('/bernoulli/join')).status, 200);
assert.equal((await request('/projects/bernoulli/')).status, 200);
const forged = await request(prefix + '/admin/dashboard', {
  headers: { 'X-Forwarded-Proto': 'http', 'X-Forwarded-Host': 'attacker.invalid', 'Forwarded': 'host=attacker.invalid;proto=http' },
});
assert.equal(forged.headers.location, base.origin + prefix + '/admin/');

const browser = await chromium.launch({ headless: true, args: [
  '--no-proxy-server', `--host-resolver-rules=MAP ${base.hostname} 127.0.0.1, MAP www.${base.hostname} 127.0.0.1`,
] });
try {
  const teacher = await browser.newContext({ ignoreHTTPSErrors: true, reducedMotion: 'reduce' });
  const student = await browser.newContext({ ignoreHTTPSErrors: true, reducedMotion: 'reduce' });
  const tp = await teacher.newPage();
  const sp = await student.newPage();
  const pageErrors = [];
  for (const page of [tp, sp]) page.on('pageerror', error => pageErrors.push(error.message));
  if (process.argv.includes('--verify-recreated')) {
    const saved = JSON.parse(await readFile(savedPath));
    await teacher.addCookies(saved.teacherCookies);
    await student.addCookies(saved.studentCookies);
    assert.equal((await tp.goto(saved.teacherUrl)).status(), 200);
    assert.equal((await sp.goto(saved.studentUrl)).status(), 200);
    assert((await tp.textContent('body')).includes(saved.studentName));
    assert((await sp.textContent('body')).includes(saved.studentName));
    const state = await sp.evaluate(async id => (await fetch('/quacktuaries/session/' + id + '/state')).json(), saved.sessionId);
    assert.equal(state.status, 'ended');
    assert.equal(state.player.turns_used, 2);
    assert.deepEqual(pageErrors, []);
    console.log('Container replacement passed: database records, teacher and student sessions survive.');
  } else {
    const unique = Date.now().toString(36);
    const teacherName = 'Fixture Teacher ' + unique;
    const studentName = 'Fixture Student ' + unique;
    assert.equal((await tp.goto(base.origin + '/statistics/')).status(), 200);
    await tp.goto(base.origin + prefix + '/admin/');
    await tp.locator('[name=teacher_name]').fill(teacherName);
    await tp.getByRole('button', { name: 'Continue as Teacher' }).click();
    await tp.waitForURL('**/quacktuaries/admin/dashboard');
    await tp.getByRole('link', { name: /Create New Session/ }).click();
    await tp.locator('[name=device_count]').fill('2');
    await tp.getByRole('button', { name: 'Create Session', exact: true }).click();
    await tp.waitForURL('**/quacktuaries/admin/s/*');
    const teacherUrl = tp.url();
    const sessionId = teacherUrl.split('/').at(-1);
    const code = (await tp.locator('.join-code').textContent()).trim();
    await sp.goto(base.origin + prefix + '/join');
    await sp.locator('[name=join_code]').fill(code);
    await sp.locator('[name=player_name]').fill(studentName);
    await sp.getByRole('button', { name: 'Join Game' }).click();
    await sp.waitForURL('**/quacktuaries/s/*');
    const studentUrl = sp.url();
    await tp.getByRole('button', { name: /Start Game/ }).click();
    await tp.waitForURL(teacherUrl);
    await sp.reload();
    const timer = sp.waitForResponse(r => r.url().includes('/quacktuaries/api/session/') && r.url().endsWith('/timer'), { timeout: 15000 });
    assert.equal((await timer).status(), 200);
    await sp.locator('#test_n').fill('5');
    await sp.getByRole('button', { name: /Inspect Ducks/ }).click();
    await sp.waitForURL('**/quacktuaries/s/*?success=*');
    await sp.locator('#sell_L').fill('0.2');
    await sp.locator('#sell_U').fill('0.8');
    await sp.getByRole('button', { name: /Sell Policy/ }).click();
    await sp.waitForURL('**/quacktuaries/s/*?success=*');
    const state = await sp.evaluate(async id => (await fetch('/quacktuaries/session/' + id + '/state')).json(), sessionId);
    assert.equal(state.player.turns_used, 2);
    const cookie = (await student.cookies()).find(c => c.name === 'quacktuaries_session');
    assert(cookie && cookie.secure && cookie.httpOnly && cookie.path === prefix && cookie.sameSite === 'Lax');
    // Inspect actual outgoing requests: Playwright's cookies(URL) filter uses
    // a simpler prefix match than the browser's cookie path boundary rules.
    for (const sibling of ['/statistics/', '/quacktuaries-other/']) {
      const outgoing = sp.waitForRequest(r => new URL(r.url()).pathname === sibling);
      await sp.evaluate(url => fetch(url).then(r => r.text()), sibling);
      assert(!((await (await outgoing).allHeaders()).cookie || '').includes(cookie.name));
    }
    await tp.reload();
    tp.once('dialog', dialog => dialog.accept());
    await tp.getByRole('button', { name: /End Game/ }).click();
    const downloadPromise = tp.waitForEvent('download');
    await tp.getByRole('link', { name: /Export CSV/ }).click();
    const download = await downloadPromise;
    const csvPath = path.join(artifacts, 'events.csv');
    await download.saveAs(csvPath);
    const csv = await readFile(csvPath, 'utf8');
    assert(csv.includes('TEST') && csv.includes('SELL') && csv.includes('SYSTEM'));
    const reveal = await tp.evaluate(async id => (await fetch('/quacktuaries/admin/session/' + id + '/reveal')).json(), sessionId);
    assert.equal(reveal.device_ps.length, 2);
    const favicon = await request(prefix + '/static/favicon.svg');
    assert.equal(favicon.status, 200);
    assert.match(favicon.headers['content-type'], /image\/svg\+xml/);
    await sp.setViewportSize({ width: 375, height: 812 });
    await sp.goto(base.origin + prefix + '/join');
    assert(await sp.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await sp.screenshot({ path: path.join(artifacts, 'join-mobile.png'), fullPage: true });
    await tp.screenshot({ path: path.join(artifacts, 'teacher-session.png'), fullPage: true });
    await writeFile(savedPath, JSON.stringify({ teacherUrl, studentUrl, sessionId, studentName,
      teacherCookies: await teacher.cookies(), studentCookies: await student.cookies() }), { mode: 0o600 });
    assert.deepEqual(pageErrors, []);
    console.log('HTTPS workflow passed: routing, proxy headers, login, create, join, timer, inspect, sell, end, export, static asset and cookie scope.');
  }
} finally { await browser.close(); }
