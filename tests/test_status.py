"""Manual deployments still report container and backup failures to the operator."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops import cli
from athenaeum_ops.common import APPS, Failure, containers_healthy


class StatusTests(unittest.TestCase):
    def test_status_checks_containers_and_backup(self):
        healthy = [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in cli.SERVICES]
        for encoding in ('array', 'lines'):
            for case in ('healthy', 'sleeping', 'crashed', 'stopped-edge', 'missing', 'unhealthy', 'stale', 'docker-failed'):
                with self.subTest(encoding=encoding, case=case), tempfile.TemporaryDirectory() as temp:
                    containers = [dict(c) for c in healthy]
                    if case == 'missing':
                        containers.pop()
                    if case == 'unhealthy':
                        containers[0]['Health'] = 'unhealthy'
                    if case in ('sleeping', 'crashed'):
                        containers[-1].update(State='exited', Health='', ExitCode=0 if case == 'sleeping' else 1)
                    if case == 'stopped-edge':
                        containers[0].update(State='exited', Health='', ExitCode=0)
                    payload = (json.dumps(containers) if encoding == 'array' else
                               '\n'.join(json.dumps(c) for c in containers)).encode()
                    Path(temp, 'status.json').write_text(json.dumps({
                        'last_backup': {'verified_at': 0 if case == 'stale' else time.time()},
                        'last_attempt': {'status': 'ok'},
                    }))
                    cfg = {'state_dir': temp, 'stale_after_seconds': 7200}
                    with patch.object(sys, 'argv', ['athenaeumctl', 'status', '--json']), \
                         patch.object(cli, 'load_config', return_value=cfg), \
                         patch.object(cli, 'operation_lock', return_value=nullcontext()), \
                         patch.object(cli, 'preflight', return_value={'free_bytes': 10**10}), \
                         patch.object(cli, 'compose', return_value=payload,
                                      side_effect=Failure('Docker unavailable') if case == 'docker-failed' else None), \
                         redirect_stdout(io.StringIO()) as output:
                        result = cli.main()
                    self.assertEqual(result, 0 if case in ('healthy', 'sleeping') else 1)
                    self.assertIn('stack', json.loads(output.getvalue()))

    def test_status_names_missing_duplicate_and_unregistered_containers(self):
        containers = [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in cli.SERVICES]
        containers.append({'Service': 'stray', 'State': 'exited', 'Health': ''})
        containers.append(dict(containers[0]))
        containers.pop(1)
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'status.json').write_text(json.dumps({'last_backup': {'verified_at': time.time()}, 'last_attempt': {'status': 'ok'}}))
            cfg = {'state_dir': temp, 'stale_after_seconds': 7200}
            with patch.object(sys, 'argv', ['athenaeumctl', 'status']), \
                 patch.object(cli, 'load_config', return_value=cfg), \
                 patch.object(cli, 'operation_lock', return_value=nullcontext()), \
                 patch.object(cli, 'preflight', return_value={'free_bytes': 10**10}), \
                 patch.object(cli, 'compose', return_value=json.dumps(containers).encode()), \
                 redirect_stdout(io.StringIO()) as output:
                result = cli.main()
        self.assertEqual(result, 1)
        self.assertIn('ERROR: unexpected container set: athenaeum missing, edge duplicate, stray unregistered', output.getvalue())

    def test_verification_requires_awake_apps_and_rejects_ambiguous_states(self):
        containers = [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in cli.SERVICES]
        for container in containers:
            if container['Service'] in APPS:
                container.update(State='exited', Health='', ExitCode=0)
        self.assertTrue(containers_healthy(containers))
        self.assertFalse(containers_healthy(containers, allow_sleeping=False))
        self.assertFalse(containers_healthy(containers + [containers[0]]))
        containers[-1].pop('ExitCode')
        self.assertFalse(containers_healthy(containers))

    def test_verify_wakes_sleeping_apps_and_requires_them_healthy_afterwards(self):
        from athenaeum_ops import verification

        def stack(app_state):
            containers = [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in cli.SERVICES]
            for container in containers:
                if container['Service'] in APPS:
                    container.update(app_state)
            return {'containers': containers}

        sleeping = {'State': 'exited', 'Health': '', 'ExitCode': 0}
        crashed = {'State': 'exited', 'Health': '', 'ExitCode': 1}
        awake = {'State': 'running', 'Health': 'healthy'}

        class Response:
            def __init__(self, url):
                self.code, self.headers = 200, {}
                if url.startswith('https://www.'):
                    self.code, self.headers = 308, {'Location': 'https://example.test/'}
                elif url.endswith('?source=setup'):
                    self.code, self.headers = 308, {'Location': url.replace('?source=setup', '/?source=setup')}
                elif url.endswith('/_health'):
                    self.code = 404
            def __enter__(self): return self
            def __exit__(self, *args): pass

        for name, before, after, error in (
                ('sleeping-then-awake', sleeping, awake, None),
                ('still-sleeping', sleeping, sleeping, 'did not become healthy'),
                ('crashed', crashed, awake, 'cleanly stopped')):
            with self.subTest(name):
                opened, commands = [], []
                opener = type('Opener', (), {'open': lambda self, url, timeout=None: opened.append((url, timeout)) or Response(url)})()
                denied = type('Result', (), {'returncode': 1, 'stderr': b'wget: connection refused'})()
                with patch.multiple(verification, preflight=lambda cfg: None, compose=lambda *a: b'edge-id',
                                    run=lambda args: commands.append(args),
                                    validate_compose=lambda cfg: {'services': {'edge': {'environment': {'SITE_DOMAIN': 'example.test'}}}},
                                    stack_status=Mock(side_effect=[stack(before), stack(after)])), \
                     patch('urllib.request.build_opener', return_value=opener), \
                     patch('subprocess.run', return_value=denied), \
                     redirect_stdout(io.StringIO()) as output, \
                     (self.assertRaisesRegex(Failure, error) if error else nullcontext()):
                    verification.verify({})
                urls = [url for url, _ in opened]
                if error == 'cleanly stopped':
                    self.assertEqual(urls, [])
                    continue
                self.assertIn('wakes sleeping apps', output.getvalue())
                for app in APPS.values():
                    prefix = 'https://example.test' + app['root_path']
                    self.assertIn((prefix + '/', 70), opened)
                    self.assertIn(prefix + '/_health', urls)
                if error is None:
                    self.assertIn('metadata access denied', output.getvalue())
                    self.assertEqual(commands[0][:3], ['docker', 'exec', 'edge-id'])
                else:
                    self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
