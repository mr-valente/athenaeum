#!/usr/bin/env python3
"""Run shared-account integration in disposable Docker containers with no Sablier.

Build the accounts, two classroom apps and edge :test images first. All identities are synthetic;
the script removes only its own containers/network. No images are published.
"""
import os
import json
from pathlib import Path
import subprocess
import shutil
import socket
import sys
import time
import unittest
import uuid

import httpx
from test_ecosystem import EcosystemTests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.STDOUT, timeout=30).strip()


class ContainerEcosystem(EcosystemTests):
    images = {'accounts': 'athenaeum-accounts:test', 'bernoulli': 'bernoulli-accounts:test',
              'quacktuaries': 'quacktuaries-accounts:test'}

    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.https_port = sock.getsockname()[1]
        cls.network = 'athenaeum-accounts-test-' + uuid.uuid4().hex[:12]
        docker('network', 'create', cls.network)
        try:
            super().setUpClass()
        except Exception:
            subprocess.run(['docker', 'network', 'rm', cls.network], capture_output=True)
            raise

    @classmethod
    def start(cls, name):
        env, _, _ = cls.envs[name]
        env = {k: v for k, v in env.items() if k in {
            'APP_ENV', 'ROOT_PATH', 'DB_PATH', 'PORT', 'SESSION_SECRET_FILE', 'FORWARDED_ALLOW_IPS',
            'ACCOUNT_SERVICE_URL', 'ATHENAEUM_APP_ID', 'ACCOUNT_ORIGIN', 'ACCOUNTS_APP_SECRETS'}}
        env.update(PORT='8000', FORWARDED_ALLOW_IPS='*')
        if name == 'accounts':
            env['ACCOUNT_ORIGIN'] = f'https://localhost:{cls.https_port}'
        if name != 'accounts':
            env['ACCOUNT_SERVICE_URL'] = 'http://accounts:8000'
        container = cls.network + '-' + name
        args = ['run', '--detach', '--pull', 'never', '--name', container,
                '--network', cls.network, '--network-alias', name, '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--user', f'{os.getuid()}:{os.getgid()}', '--memory', '512m',
                '--tmpfs', '/tmp:size=16777216,mode=1777,noexec,nosuid,nodev',
                '-p', f'127.0.0.1:{cls.ports[name]}:8000',
                '--mount', f'type=bind,src={cls.directory / name},dst={cls.directory / name}']
        for secret in (('accounts', 'bernoulli', 'quacktuaries') if name == 'accounts' else (name,)):
            path = cls.directory / (secret + '-secret')
            args += ['--mount', f'type=bind,src={path},dst={path},readonly']
        for key, value in env.items():
            args += ['--env', key + '=' + value]
        docker(*args, cls.images[name])
        cls.processes[name] = container
        for _ in range(150):
            try:
                if httpx.get(f'http://127.0.0.1:{cls.ports[name]}/_health').status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.05)
        raise RuntimeError(name + ' did not become ready: ' + docker('logs', container)[-2000:])

    @classmethod
    def stop(cls, name):
        container = cls.processes.pop(name, None)
        if container:
            docker('rm', '--force', container)

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            docker('network', 'rm', cls.network)

    def test_https_account_navigation_cookies_and_private_routes(self):
        root = Path(__file__).resolve().parents[1]
        snippets = self.directory / 'apps.caddy'
        # The production routes are unchanged; bypass lifecycle management only.
        # This daemon may already have a Sablier manager, so never launch another.
        snippets.write_text('''(light) {
            handle {args[0]} {
                redir {args[0]}/ 308
            }
            handle {args[0]}/* {
                route {
                    respond {args[0]}/_health 404
                    uri strip_prefix {args[0]}
                    reverse_proxy {args[1]}:8000
                }
            }
        }
        ''')
        website = self.network + '-website'
        docker('run', '-d', '--pull', 'never', '--name', website, '--network', self.network,
               '--network-alias', 'athenaeum', '--read-only', '--cap-drop', 'ALL',
               '--mount', f'type=bind,src={root / "dist"},dst=/srv,readonly',
               'athenaeum-accounts-edge:test', 'caddy', 'file-server', '--listen', ':8080', '--root', '/srv')
        self.processes['website'] = website
        edge = self.network + '-edge'
        docker('run', '-d', '--pull', 'never', '--name', edge, '--network', self.network,
               '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--tmpfs', '/data:uid=10001,gid=10001', '--tmpfs', '/config:uid=10001,gid=10001',
               '--mount', f'type=bind,src={snippets},dst=/etc/caddy/apps.caddy,readonly',
               '-e', 'SITE_DOMAIN=localhost', '-e', f'LOCAL_HTTPS_PORT={self.https_port}',
               '-p', f'127.0.0.1:{self.https_port}:8443',
               'athenaeum-accounts-edge:test', 'caddy', 'run', '--config', '/etc/caddy/Caddyfile.local')
        self.processes['edge'] = edge
        base = f'https://localhost:{self.https_port}'
        for _ in range(100):
            try:
                if httpx.get(base + '/account/', verify=False).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.1)
        else:
            self.fail(docker('logs', edge)[-2000:])
        for query in ('', '?source=setup', '?return_to=%2Fbernoulli%2F&source=setup',
                      '?return_to=%2Faccount%2Fexport'):
            response = httpx.get(base + '/account' + query, verify=False, follow_redirects=False)
            self.assertEqual(response.status_code, 308)
            self.assertEqual(response.headers['location'], '/account/' + query)
        for path in ('/internal/identity', '/account/_health', '/bernoulli/_health', '/quacktuaries/_health'):
            self.assertEqual(httpx.get(base + path, verify=False).status_code, 404)
        screenshot = '/tmp/athenaeum-account-overview.png'
        browser_image = os.environ.get('ATHENAEUM_BROWSER_IMAGE')
        if browser_image:
            # Reuse browser libraries from an existing image without changing the host.
            # The mounted Node executable and Chromium match the pinned test package.
            browser_cache = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', Path.home() / '.cache/ms-playwright'))
            args = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'host',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--read-only',
                    '--tmpfs', '/tmp:mode=1777', '--shm-size', '256m',
                    '--mount', f'type=bind,src={Path(shutil.which("node")).resolve()},dst=/usr/local/bin/athenaeum-node,readonly',
                    '--mount', f'type=bind,src={browser_cache},dst=/browsers,readonly',
                    '--mount', f'type=bind,src={root / "tests/browser"},dst=/checks,readonly',
                    '--mount', f'type=bind,src={self.directory},dst=/artifacts',
                    '-e', 'PLAYWRIGHT_BROWSERS_PATH=/browsers', '-e', 'TEST_BASE_URL=' + base,
                    '-e', 'TEST_ACCOUNT_SCREENSHOT=/artifacts/account.png', browser_image,
                    '/usr/local/bin/athenaeum-node', '/checks/accounts.mjs']
            subprocess.run(args, check=True, timeout=45)
            shutil.copyfile(self.directory / 'account.png', screenshot)
        else:
            env = dict(os.environ, TEST_BASE_URL=base, TEST_ACCOUNT_SCREENSHOT=screenshot)
            subprocess.run(['node', str(root / 'tests/browser/accounts.mjs')], env=env, check=True, timeout=45)

    def test_google_credentials_activate_after_replacement(self):
        from accounts.configure_google import configure
        url = f'http://127.0.0.1:{self.ports["accounts"]}/account/'
        self.assertNotIn('class="google-signin"', httpx.get(url).text)
        secret_file = self.directory / 'accounts-secret'
        original = secret_file.read_text().strip()
        secret_file.chmod(0o600)
        configure(secret_file, 'container-fixture.apps.googleusercontent.com', 'synthetic-client-secret')
        self.assertEqual(json.loads(secret_file.read_text())['session_secret'], original)
        # The running service still has its old file mount and startup settings.
        self.assertNotIn('class="google-signin"', httpx.get(url).text)
        self.stop('accounts')
        self.start('accounts')
        self.assertIn('class="google-signin"', httpx.get(url).text)
        # No Google request or credentials from a real account are involved.


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromTestCase(ContainerEcosystem))
    raise SystemExit(0 if result.wasSuccessful() else 1)
