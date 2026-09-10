"""The host report never blocks on the lock, never touches recovery points, and says what status says."""
from contextlib import redirect_stdout
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops import cli, report
from athenaeum_ops.common import SERVICES, Failure, atomic_json, load_config
from athenaeum_ops.storage import LocalStore, OCIStore


def healthy_containers():
    return [{'Service': name, 'State': 'running', 'Health': 'healthy', 'ExitCode': 0} for name in SERVICES]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('live', 'state', 'objects'):
            (self.root / name).mkdir(mode=0o700)
        for name in ('secret', 'bernoulli-secret', 'compose.env', 'compose.yaml'):
            (self.root / name).write_text('fixture-' * 8)
            (self.root / name).chmod(0o600)
        self.config = self.root / 'recovery.json'
        self.write_config()

    def write_config(self, **overrides):
        cfg = {'schema': 1, 'mode': 'local', 'filesystem_uuid': '', 'data_root': str(self.root / 'live'),
               'state_dir': str(self.root / 'state'), 'compose_project': 'fixture',
               'compose_files': [str(self.root / 'compose.yaml')], 'compose_env': str(self.root / 'compose.env'),
               'session_secret': str(self.root / 'secret'), 'bernoulli_session_secret': str(self.root / 'bernoulli-secret'),
               'recipient': 'age1' + 'a' * 58, 'age_binary': '/bin/true', 'stale_after_seconds': 7200,
               'store': {'kind': 'local', 'directory': str(self.root / 'objects'), 'prefix': 'athenaeum/v1/'}, **overrides}
        atomic_json(self.config, cfg)
        self.cfg = load_config(self.config)
        return self.cfg


class ReportTests(Fixture):
    def test_configuration_defaults_on_and_rejects_names_under_the_backup_prefix(self):
        self.assertEqual(self.cfg['monitor_object'], 'monitor-host.json')
        self.assertIsNone(self.write_config(monitor_object=None)['monitor_object'])
        for bad in ('athenaeum/v1/monitor.json', '', 'a b', 3):
            with self.subTest(bad=bad), self.assertRaises(Failure):
                self.write_config(monitor_object=bad)

    def test_report_carries_disks_backup_status_and_containers(self):
        now = time.time()
        atomic_json(self.root / 'state/status.json', {
            'last_backup': {'verified_at': now - 600, 'snapshot': '20260910T150000Z-abc'},
            'last_attempt': {'at': now - 590, 'status': 'ok'},
            'last_restore_test': {'at': now - 86400 * 3, 'status': 'ok'}})
        containers = healthy_containers()
        containers[-1].update(State='exited', Health='', ExitCode=0)  # an app asleep is fine, as for status
        with patch.object(cli, 'stack_status', return_value={'containers': containers}):
            doc = report.build_report(self.cfg, now=now)
        self.assertEqual(doc['schema'], 1)
        self.assertEqual(doc['generated_at'], time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)))
        self.assertEqual(set(doc['filesystems']), {'/', self.cfg['data_root']})
        root = doc['filesystems']['/']
        self.assertTrue(root['mounted'])
        self.assertGreater(root['total_bytes'], root['used_bytes'])
        self.assertEqual(root['used_percent'], round(root['used_bytes'] / root['total_bytes'] * 100, 1))
        self.assertFalse(doc['filesystems'][self.cfg['data_root']]['mounted'])
        backup = doc['backup']
        self.assertFalse(backup['stale'])
        self.assertEqual(backup['snapshot'], '20260910T150000Z-abc')
        self.assertEqual(backup['last_attempt_status'], 'ok')
        self.assertIsNone(backup['last_attempt_error'])
        self.assertTrue(backup['verified_at'].endswith('Z') and backup['restore_test_at'].endswith('Z'))
        self.assertEqual([c['service'] for c in doc['containers']['services']], list(SERVICES))
        self.assertEqual(doc['containers']['services'][-1], {'service': SERVICES[-1], 'name': None, 'state': 'exited', 'health': None,
                                                             'exit_code': 0, 'issue': None, 'ok': True})
        self.assertTrue(all(c['ok'] for c in doc['containers']['services']))
        self.assertTrue(doc['containers']['ok'])
        self.assertFalse(doc['containers']['busy'])

    def test_container_set_problems_are_named(self):
        containers = healthy_containers()
        containers.append({'Service': 'stray', 'Name': 'athenaeum-stray-1', 'State': 'running', 'Health': 'healthy'})
        containers.append({**containers[0], 'Name': 'athenaeum-edge-2'})
        edge = containers.pop(0)  # the first edge is now the duplicate; remove the original
        containers.insert(0, edge)
        athenaeum = next(c for c in containers if c['Service'] == 'athenaeum')
        containers.remove(athenaeum)
        with patch.object(cli, 'stack_status', return_value={'containers': containers}):
            doc = report.build_report(self.cfg)
        self.assertFalse(doc['containers']['ok'])
        flagged = sorted((c['service'], c['issue']) for c in doc['containers']['services'] if not c['ok'])
        self.assertEqual(flagged, [('athenaeum', 'missing'), ('edge', 'duplicate'), ('edge', 'duplicate'), ('stray', 'unregistered')])
        self.assertEqual(next(c for c in doc['containers']['services'] if c['service'] == 'stray')['name'], 'athenaeum-stray-1')
        self.assertEqual(len(doc['containers']['services']), len(containers) + 1)

    def test_missing_status_and_docker_failure_are_reported_not_raised(self):
        with patch.object(cli, 'stack_status', return_value={'containers': [], 'error': 'Docker unavailable'}):
            doc = report.build_report(self.cfg)
        self.assertTrue(doc['backup']['stale'])
        self.assertIsNone(doc['backup']['verified_at'])
        self.assertEqual(doc['containers'], {'busy': False, 'services': None, 'ok': None, 'error': 'Docker unavailable'})
        atomic_json(self.root / 'state/status.json', {'last_attempt': {'at': 1, 'status': 'failed', 'error': 'Insufficient free space'}})
        containers = healthy_containers()
        containers[0].update(State='exited', ExitCode=0)  # the edge never sleeps
        with patch.object(cli, 'stack_status', return_value={'containers': containers}):
            doc = report.build_report(self.cfg)
        self.assertEqual(doc['backup']['last_attempt_error'], 'Insufficient free space')
        self.assertFalse(doc['containers']['ok'])
        self.assertEqual([c['service'] for c in doc['containers']['services'] if not c['ok']], ['edge'])

    def test_held_operation_lock_is_never_waited_for(self):
        lock = self.root / 'state/operation.lock'
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with patch.object(cli, 'stack_status', side_effect=AssertionError('asked Docker during an operation')):
            doc = report.build_report(self.cfg)
        self.assertEqual(doc['containers'], {'busy': True, 'services': None, 'ok': None})
        # The probe releases at once: the lock is still ours, and the file is untouched.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(cli, 'stack_status', return_value={'containers': healthy_containers()}):
            self.assertTrue(report.build_report(self.cfg)['containers']['busy'])

    def test_publish_overwrites_one_bare_object_outside_the_prefix(self):
        with patch.object(cli, 'stack_status', return_value={'containers': healthy_containers()}):
            first = report.publish_report(self.cfg)
            second = report.publish_report(self.cfg)
        self.assertEqual(first['published'], 'monitor-host.json')
        published = self.root / 'objects/monitor-host.json'
        self.assertEqual(published.stat().st_size, second['bytes'])
        self.assertEqual(published.stat().st_mode & 0o777, 0o600)
        doc = json.loads(published.read_text())
        self.assertEqual(doc['schema'], 1)
        self.assertEqual([p.name for p in (self.root / 'objects').iterdir()], ['monitor-host.json'])
        store = LocalStore(self.cfg['store'])
        for bad in ('athenaeum/v1/commits/x.json', '../escape', ''):
            with self.subTest(bad=bad), self.assertRaises(Failure):
                store.publish(bad, b'{}')

    def test_disabled_report_refuses_to_publish(self):
        self.write_config(monitor_object=None)
        with self.assertRaises(Failure):
            report.publish_report(self.cfg)

    def test_oracle_publish_is_an_unconditional_json_put(self):
        client = NS(calls=[])
        client.put_object = lambda ns, bucket, name, body, **kw: client.calls.append((ns, bucket, name, body, kw))
        OCIStore({'namespace': 'ns', 'bucket': 'backups'}, client).publish('monitor-host.json', b'{"schema":1}')
        (ns, bucket, name, body, kw), = client.calls
        self.assertEqual((ns, bucket, name, body), ('ns', 'backups', 'monitor-host.json', b'{"schema":1}'))
        self.assertEqual(kw, {'content_length': 12, 'content_type': 'application/json'})

    def test_cli_report_runs_outside_the_operation_lock(self):
        def locked(cfg):
            raise AssertionError('report took the operation lock')
        for argv, published in ((['athenaeumctl', '--config', str(self.config), 'report', '--print'], False),
                                (['athenaeumctl', '--config', str(self.config), 'report'], True)):
            with self.subTest(argv=argv), patch.object(sys, 'argv', argv), \
                 patch.object(cli, 'operation_lock', locked), \
                 patch.object(cli, 'stack_status', return_value={'containers': healthy_containers()}), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual('published' in result, published)
            self.assertEqual((self.root / 'objects/monitor-host.json').exists(), published)


if __name__ == '__main__':
    unittest.main()
