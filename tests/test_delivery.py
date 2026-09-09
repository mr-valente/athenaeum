"""Deployment state transitions exercised with a deterministic host boundary."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops.common import Failure
from athenaeum_ops.delivery import Updater, pause
from athenaeum_ops.releases import ReleaseClient, compatible, json_data, validate_descriptor


def release(name, generation=1):
    return {'schema': 1, 'service': name, 'repository': 'fixture/' + ('athenaeum' if name == 'edge' else name),
        'source_commit': 'a' * 40, 'version': f'build-{generation}',
        'image': f'docker.io/fixture/{name}@sha256:' + str(generation) * 64,
        'style_version': 'unselected', 'compatibility': {'config': 1, 'read_min': 0, 'read_max': 0, 'write': 0},
        'migration': 'none', 'created_at': f'2026-01-{generation:02d}T00:00:00+00:00'}


class Client:
    def __init__(self):
        self.releases = {s: release(s) for s in ('edge', 'athenaeum', 'quacktuaries')}
    def fetch(self, service, allowed):
        return copy.deepcopy(self.releases[service])


class FakeHost:
    def __init__(self):
        self.calls, self.active, self.held = [], False, False
        self.fail_health, self.fail_backup, self.fail_pull = None, False, False
        self.mutate_schema, self.class_after_pull = False, False
        self.schema, self.hash, self.rows = 0, 'original', 12
    def preflight(self): pass
    def empty(self): pass
    def verify_current(self, *args): pass
    def pull(self, descriptor):
        self.calls.append(('pull', descriptor['service']))
        if self.fail_pull: raise Failure('Registry unavailable')
        if self.class_after_pull: self.active = True
    def up(self, service=None):
        self.calls.append(('up', service))
        if self.mutate_schema: self.hash = 'mutated'
    def stop_initial(self): self.calls.append(('stop',))
    def remove_image(self, reference): self.calls.append(('remove', reference))
    def ready(self, service):
        self.calls.append(('ready', service))
        if self.fail_health == service:
            self.fail_health = None
            self.rows += 1  # New writes must never be overwritten to roll back.
            raise Failure('Unhealthy')
    def activity(self, command='status'):
        if command == 'drain' and not self.active: self.held = True
        if command == 'release': self.held = False
        return {'protocol': 1, 'active_class': self.active, 'data_schema': self.schema, 'held': self.held}
    def database(self): return {'user_version': self.schema, 'schema_sha256': self.hash, 'row_counts': {'events': self.rows}}
    def backup(self):
        self.calls.append(('backup',))
        if self.fail_backup: raise Failure('Backup capacity')
        return {'snapshot': 'fixture'}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data/deploy-state').mkdir(parents=True)
        (self.root / 'state').mkdir(mode=0o700)
        self.cfg = {'mode': 'local', 'data_root': str(self.root / 'data'), 'state_dir': str(self.root / 'state')}
        self.client, self.host = Client(), FakeHost()
        self.updates = {'window': None, 'services': {s: {'repository': d['repository'], 'image': d['image'].split('@')[0], 'automatic': s != 'edge'} for s, d in self.client.releases.items()}}
        self.updater = Updater(self.cfg, self.updates, self.host, self.client)
        self.updater.run(initial=True)
        self.host.calls.clear()

    def candidate(self, name='quacktuaries'):
        self.client.releases[name] = release(name, 2)

    def test_success_replaces_only_affected_app_and_keeps_previous(self):
        self.candidate()
        self.assertEqual(self.updater.run()['services']['quacktuaries'], 'updated')
        self.assertEqual([c for c in self.host.calls if c[0] == 'up'], [('up', 'quacktuaries')])
        self.assertLess(self.host.calls.index(('backup',)), self.host.calls.index(('up', 'quacktuaries')))
        self.assertEqual(self.updater.state['services']['quacktuaries']['previous'], release('quacktuaries'))
        self.assertFalse(self.host.held)
        overlay = json.loads(self.updater.overlay.read_bytes())['services']
        self.assertEqual(overlay['edge']['image'], release('edge')['image'])

    def test_active_class_defers_without_pull_or_backup(self):
        self.candidate()
        self.host.active = True
        self.assertEqual(self.updater.run()['services']['quacktuaries'], 'active-class-deferred')
        self.assertEqual(self.host.calls, [])

    def test_class_starting_during_pull_is_deferred_by_drain(self):
        self.candidate()
        self.host.class_after_pull = True
        self.assertEqual(self.updater.run()['services']['quacktuaries'], 'active-class-deferred')
        self.assertFalse(any(c[0] in ('up', 'backup') for c in self.host.calls))

    def test_failed_health_rolls_back_preserves_writes_and_holds_digest_across_restart(self):
        self.candidate()
        self.host.fail_health = 'quacktuaries'
        with self.assertRaisesRegex(Failure, 'previous compatible image restored'):
            self.updater.run()
        self.assertEqual(self.host.rows, 13)
        self.assertEqual(self.updater.state['services']['quacktuaries']['current'], release('quacktuaries'))
        self.assertIsNone(self.updater.state['transaction'])
        again = Updater(self.cfg, self.updates, self.host, self.client)
        self.host.calls.clear()
        self.assertEqual(again.run()['services']['quacktuaries'], 'failed-digest-held')
        self.assertEqual(self.host.calls, [])

    def test_failed_backup_never_changes_images_and_releases_gate(self):
        self.candidate()
        self.host.fail_backup = True
        old = self.updater.overlay.read_bytes()
        with self.assertRaisesRegex(Failure, 'Backup capacity'): self.updater.run()
        self.assertEqual(self.updater.overlay.read_bytes(), old)
        self.assertFalse(self.host.held)
        self.assertIsNone(self.updater.state['transaction'])

    def test_pause_survives_new_process_and_resume_does_not_deploy(self):
        self.candidate()
        pause(self.cfg, True)
        other = Updater(self.cfg, self.updates, self.host, self.client)
        self.assertEqual(other.run(), {'status': 'paused'})
        pause(self.cfg, False)
        self.assertEqual(self.host.calls, [])
        self.assertEqual(other.run()['services']['quacktuaries'], 'updated')

    def test_destructive_or_nonrollbackable_schema_refused_before_pull(self):
        self.candidate()
        self.client.releases['quacktuaries']['migration'] = 'destructive'
        with self.assertRaisesRegex(Failure, 'Destructive'): self.updater.run()
        self.assertEqual(self.host.calls, [])
        d = self.client.releases['quacktuaries']
        d['migration'] = 'backward-compatible'
        d['compatibility'].update(write=1, read_max=1)
        with self.assertRaisesRegex(Failure, 'automatic rollback is unsafe'): self.updater.run()
        self.assertEqual(self.host.calls, [])

    def test_undeclared_schema_change_leaves_transaction_for_manual_recovery(self):
        self.candidate()
        self.host.mutate_schema = True
        with self.assertRaisesRegex(Failure, 'Unexpected schema mutation'): self.updater.run()
        self.assertIsNotNone(self.updater.state['transaction'])
        self.assertEqual([c for c in self.host.calls if c[0] == 'up'], [('up', 'quacktuaries')])
        with self.assertRaisesRegex(Failure, 'Interrupted deployment'): self.updater.run()

    def test_registry_failure_has_backoff_without_touching_container(self):
        self.candidate()
        self.host.fail_pull = True
        with self.assertRaisesRegex(Failure, 'Registry'): self.updater.run()
        self.host.calls.clear()
        self.assertEqual(self.updater.run()['services']['quacktuaries'], 'registry-backoff')
        self.assertEqual(self.host.calls, [])

    def test_operator_rollback_holds_reverted_release(self):
        self.candidate('athenaeum')
        self.updater.run()
        self.assertEqual(self.updater.rollback('athenaeum')['status'], 'rolled-back')
        self.assertEqual(self.updater.run()['services']['athenaeum'], 'failed-digest-held')

    def test_edge_is_manual_and_protects_class(self):
        self.candidate('edge')
        self.updater.run()
        self.assertEqual(self.host.calls, [])
        self.host.active = True
        self.assertEqual(self.updater.run(service='edge')['services']['edge'], 'active-class-deferred')

    def test_image_cleanup_only_removes_superseded_owned_release(self):
        self.candidate('athenaeum')
        self.updater.run()
        self.client.releases['athenaeum'] = release('athenaeum', 3)
        self.updater.run()
        removed = [c[1] for c in self.host.calls if c[0] == 'remove']
        self.assertEqual(removed, [release('athenaeum')['image']])
        self.assertEqual(self.updater.state['services']['athenaeum']['previous'], release('athenaeum', 2))

    def test_interrupted_transaction_requires_explicit_compatible_recovery(self):
        old, new = release('athenaeum'), release('athenaeum', 2)
        self.updater.state['transaction'] = {'kind': 'update', 'service': 'athenaeum', 'previous': old,
            'candidate': new, 'database_before': None}
        self.updater.save()
        self.updater.images({'athenaeum': new})
        other = Updater(self.cfg, self.updates, self.host, self.client)
        with self.assertRaisesRegex(Failure, 'Interrupted'): other.run()
        self.assertEqual(other.rollback('athenaeum')['status'], 'recovered')
        self.assertEqual(json.loads(other.overlay.read_bytes())['services']['athenaeum']['image'], old['image'])


class DescriptorTests(unittest.TestCase):
    def test_rejects_duplicate_fields_untrusted_images_commands_and_compatibility(self):
        d = release('quacktuaries')
        allowed = {'repository': d['repository'], 'image': d['image'].split('@')[0]}
        validate_descriptor(d, 'quacktuaries', allowed)
        with self.assertRaises(Failure): json_data('{"schema":1,"schema":1}')
        for field, value in [('image', 'evil.invalid/image:latest'), ('repository', 'other/repo'),
                             ('schema', 2), ('command', 'echo unsafe'), ('created_at', '2099-01-01T00:00:00Z')]:
            with self.subTest(field=field), self.assertRaises(Failure):
                validate_descriptor(dict(d, **{field: value}), 'quacktuaries', allowed)

    def test_registry_metadata_is_pinned_and_discovery_backs_off(self):
        with tempfile.TemporaryDirectory() as temp:
            d = release('quacktuaries')
            allowed = {'image': d['image'].split('@')[0]}
            index = {'annotations': {
                'org.opencontainers.image.source': 'https://github.com/' + d['repository'],
                'org.opencontainers.image.revision': d['source_commit'],
                'org.opencontainers.image.version': d['version'],
                'org.opencontainers.image.created': d['created_at'],
                'io.valentemath.style': d['style_version'], 'io.valentemath.schema': '0'}}
            manifest = {'digest': d['image'].split('@')[1], 'manifests': [
                {'platform': {'os': 'linux', 'architecture': a}} for a in ('amd64', 'arm64')]}
            with patch('athenaeum_ops.releases.run', return_value=json.dumps(manifest | index).encode()) as execute:
                client = ReleaseClient({'state_dir': temp})
                self.assertEqual(client.fetch('quacktuaries', allowed), d)
                self.assertIsNone(client.fetch('quacktuaries', allowed))
                self.assertEqual(execute.call_count, 1)
            client.cache[allowed['image']]['next_attempt'] = 0
            with patch('athenaeum_ops.releases.run', side_effect=Failure('Registry unavailable')):
                with self.assertRaisesRegex(Failure, 'Registry'): client.fetch('quacktuaries', allowed)
            other = ReleaseClient({'state_dir': temp})
            with patch('athenaeum_ops.releases.run') as execute:
                self.assertIsNone(other.fetch('quacktuaries', allowed))
                execute.assert_not_called()

    def test_missing_platform_or_annotations_cannot_be_deployed(self):
        for payloads in ([{'digest': 'sha256:' + 'a'*64, 'manifests': []}],
                         [{'digest': 'sha256:' + 'a'*64, 'manifests': [
                              {'platform': {'os': 'linux', 'architecture': a}} for a in ('amd64', 'arm64')]}, {}]):
            with tempfile.TemporaryDirectory() as temp, patch('athenaeum_ops.releases.run', side_effect=[json.dumps(p).encode() for p in payloads]):
                with self.assertRaises(Failure):
                    ReleaseClient({'state_dir': temp}).fetch('quacktuaries', {'image': 'docker.io/fixture/quacktuaries'})

    def test_update_allowlist_comes_from_compose_without_repository_catalog(self):
        from athenaeum_ops.releases import load_updates
        services = {s: {'image': 'docker.io/fixture/' + s + ':latest'} for s in ('edge', 'athenaeum', 'quacktuaries')}
        services['edge']['environment'] = {'SITE_DOMAIN': 'valentemath.com'}
        with patch('athenaeum_ops.releases.compose', return_value=json.dumps({'services': services}).encode()):
            value = load_updates({'mode': 'production', 'min_free_bytes': 123})
        self.assertEqual(value['origin'], 'https://valentemath.com')
        self.assertFalse(value['services']['edge']['automatic'])
        self.assertTrue(value['services']['quacktuaries']['automatic'])
        self.assertEqual(value['services']['athenaeum']['image'], 'docker.io/fixture/athenaeum')


class StatusTests(unittest.TestCase):
    def test_human_status_highlights_missing_apps_stale_backup_and_failure(self):
        from athenaeum_ops.cli import print_status
        from contextlib import redirect_stdout
        import io
        result = {'backup_stale': True, 'storage': {'free_bytes': 4_000_000_000},
                  'delivery': {'paused': True, 'containers': [], 'releases': {
                      'last_error': {'error': 'Registry unavailable'}, 'transaction': {'kind': 'update'}}}}
        with redirect_stdout(io.StringIO()) as out:
            print_status(result)
        text = out.getvalue()
        for fragment in ['Updates: paused', 'Backup: none yet (STALE)', '4.0 GB free',
                         'quacktuaries: missing', 'interrupted deployment', 'Registry unavailable']:
            self.assertIn(fragment, text)


class RegistryBoundaryTests(unittest.TestCase):
    def test_missing_architecture_or_wrong_pulled_digest_never_starts_container(self):
        from athenaeum_ops.delivery import Host
        descriptor = release('quacktuaries')
        host = Host({}, {})
        host.arch = 'arm64'
        calls = []
        def execute(args, **kwargs):
            calls.append(args)
            if args[:4] == ['docker', 'buildx', 'imagetools', 'inspect']:
                return json.dumps({'manifests': [{'platform': {'os': 'linux', 'architecture': 'amd64'}}]}).encode()
            self.fail('Missing architecture must refuse before pull')
        with patch('athenaeum_ops.delivery.run', side_effect=execute), self.assertRaisesRegex(Failure, 'missing required'):
            host.pull(descriptor)
        self.assertEqual(len(calls), 1)
        def wrong_digest(args, **kwargs):
            if args[:4] == ['docker', 'buildx', 'imagetools', 'inspect']:
                return json.dumps({'manifests': [{'platform': {'os':'linux','architecture':a}} for a in ('amd64','arm64')]}).encode()
            if args[:2] == ['docker', 'pull']: return b''
            return json.dumps([{'RepoDigests':['docker.io/fixture/quacktuaries@sha256:'+'f'*64], 'Architecture':'arm64','Os':'linux'}]).encode()
        with patch('athenaeum_ops.delivery.run', side_effect=wrong_digest), self.assertRaisesRegex(Failure, 'does not match'):
            host.pull(descriptor)

    def test_https_probe_rejects_redirect_despite_healthy_container(self):
        from athenaeum_ops.delivery import Host
        host = Host({}, {'readiness_seconds': 1, 'origin': 'https://valentemath.com', 'ca_cert': None})
        with patch.object(host, 'container', return_value={'State': {'Running': True, 'Health': {'Status':'healthy'}}}), \
             patch('athenaeum_ops.delivery.run', return_value=b'302'), \
             patch('athenaeum_ops.delivery.time.monotonic', side_effect=[0, 2]), self.assertRaisesRegex(Failure, 'readiness'):
            host.ready('quacktuaries')


if __name__ == '__main__': unittest.main()
