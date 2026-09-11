import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { parse } from 'yaml';

const compose = parse(readFileSync(new URL('../compose.yaml', import.meta.url), 'utf8'));
const config = parse(readFileSync(new URL('../deploy/sablier/sablier.yaml', import.meta.url), 'utf8'));
const snippets = readFileSync(new URL('../deploy/caddy/apps.caddy', import.meta.url), 'utf8');
const routes = readFileSync(new URL('../deploy/caddy/routes.caddy', import.meta.url), 'utf8');

// The session tiers and their defaults; the table in skills/athenaeum-app/SKILL.md must agree.
const tiers: Record<string, string> = { light: '72h', middle: '12h', heavy: '4h', superheavy: '1h' };

test('only app services opt in, with independent project-qualified groups', () => {
  const managed = Object.entries(compose.services).filter(([, service]: [string, any]) => service.labels?.['sablier.enable'] === 'true');
  assert.deepEqual(managed.map(([name]) => name), ['quacktuaries', 'bernoulli']);
  const groups = managed.map(([name, service]: [string, any]) => {
    assert.equal(service.labels['sablier.group'], '${COMPOSE_PROJECT_NAME:-athenaeum}-' + name);
    assert.deepEqual(service.networks, ['apps']);
    assert.equal(service.ports, undefined);
    return service.labels['sablier.group'];
  });
  assert.equal(new Set(groups).size, managed.length);
  assert.equal(compose.services.edge.environment.SABLIER_GROUP_PREFIX, '${COMPOSE_PROJECT_NAME:-athenaeum}');
});

test('privileged lifecycle API is isolated from application peers', () => {
  assert.deepEqual(compose.services.sablier.networks, ['control']);
  assert.equal(compose.services.sablier.ports, undefined);
  assert.equal(compose.networks.control.internal, true);
  assert.deepEqual(Object.entries(compose.services).filter(([, service]: [string, any]) => service.networks.includes('control')).map(([name]) => name), ['edge', 'sablier']);
  for (const [name, service] of Object.entries<any>(compose.services)) {
    const sockets = (service.volumes ?? []).filter((mount: any) => mount.target === '/var/run/docker.sock');
    assert.equal(sockets.length, name === 'sablier' ? 1 : 0);
  }
  for (const mount of compose.services.sablier.volumes) {
    assert.equal(mount.read_only, true);
    assert.equal(mount.bind.create_host_path, false);
  }
  assert.deepEqual(compose.services.sablier.healthcheck.test, ['CMD', '/bin/sablier', 'health']);
});

test('every managed app takes its session from a tier, and each tier carries its default', () => {
  for (const [tier, duration] of Object.entries(tiers)) {
    const line = `(${tier}) {\n\timport app {args[0]} {args[1]} {args[2]} {$SABLIER_SESSION_${tier.toUpperCase()}:${duration}}\n}`;
    assert.ok(snippets.includes(line), `${tier} tier snippet with a ${duration} default`);
  }
  // Both the dynamic and the blocking directive pass the tier's duration on every request.
  assert.equal(snippets.match(/session_duration \{args\[3\]\}/g)?.length, 2);
  const routed = new Map(routes.split('\n').filter((line) => line.startsWith('import ')).map((line) => {
    const [, tier, prefix, service, name] = line.split(' ');
    assert.ok(tier in tiers, `${service} names a tier`);
    assert.equal(prefix, '/' + service);
    assert.ok(name);
    return [service, tier];
  }));
  const managed = Object.entries(compose.services).filter(([, service]: [string, any]) => service.labels?.['sablier.enable'] === 'true').map(([name]) => name);
  assert.deepEqual([...routed.keys()], managed);
  assert.deepEqual([...routed.values()], ['light', 'light']);
});

test('the shortest tier is the default for adopted apps; verify renews them at their tier', () => {
  assert.equal(config.sessions['default-duration'], Object.values(tiers).at(-1));
  assert.equal(config.provider['auto-stop-on-startup'], false);
  assert.equal(config.provider['auto-warm-externally-started'], true); // also the self-heal for the 1.18.0 lost-expiry race
  assert.equal(config.provider['reject-unlabeled-requests'], true);
  assert.equal(config.provider['verify-enabled-on-expiration'], true);
  assert.equal(config.provider.docker.strategy, 'stop');
  assert.equal(config.strategy.dynamic['default-theme'], 'athenaeum');
  assert.equal(config.strategy.dynamic['show-details-by-default'], false);
});
