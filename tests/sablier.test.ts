import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { parse } from 'yaml';

const compose = parse(readFileSync(new URL('../compose.yaml', import.meta.url), 'utf8'));
const config = parse(readFileSync(new URL('../deploy/sablier/sablier.yaml', import.meta.url), 'utf8'));

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

test('one 12-hour default applies to requests and externally started apps', () => {
  assert.equal(config.sessions['default-duration'], '12h');
  assert.equal(config.provider['auto-stop-on-startup'], false);
  assert.equal(config.provider['auto-warm-externally-started'], true); // also the self-heal for the 1.18.0 lost-expiry race
  assert.equal(config.provider['reject-unlabeled-requests'], true);
  assert.equal(config.provider['verify-enabled-on-expiration'], true);
  assert.equal(config.provider.docker.strategy, 'stop');
  assert.equal(config.strategy.dynamic['default-theme'], 'athenaeum');
  assert.equal(config.strategy.dynamic['show-details-by-default'], false);
});
