"""Opt-in Docker smoke test; only uniquely named disposable fixtures are mutated."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parent.parent
EDGE = os.environ.get('SABLIER_TEST_EDGE_IMAGE', 'athenaeum-edge:sablier-test')


def serve():
    started = time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/_health':
                self.send_response(200 if time.monotonic() - started > 6 else 503)
                self.end_headers()
                return
            self.reply()

        def do_POST(self):
            self.reply()

        def reply(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()
            payload = json.dumps({'method': self.command, 'path': self.path, 'body': body}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
    ThreadingHTTPServer(('0.0.0.0', int(sys.argv[2])), Handler).serve_forever()


def docker(*args):
    try:
        return subprocess.check_output(['docker', *args], stderr=subprocess.PIPE, text=True).strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.strip()) from error


def wait_for(check, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.5)
    raise AssertionError('Timed out waiting for fixture state')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    prefix = 'athenaeum-smoke-' + uuid.uuid4().hex[:10]
    created = []
    docker('image', 'inspect', EDGE)
    for image in ('python:3.13-alpine', 'sablierapp/sablier:1.18.0'):
        docker('pull', image)
    existing = docker('ps', '--format', '{{.Image}} {{.Names}}')
    if 'sablier' in existing.lower():
        raise SystemExit('Use a daemon without an existing Sablier manager for the smoke test.')
    docker('network', 'create', prefix)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            config = Path(directory) / 'Caddyfile'
            config.write_text('{\n\tadmin off\n\tpersist_config off\n}\n\nimport /etc/caddy/apps.caddy\n\n:8080 {\n'
                              '\timport /etc/caddy/routes.caddy\n}\n')

            def create(service, image, options=(), command=(), start=False):
                name = prefix + '-' + service
                docker('create', '--name', name, '--network', prefix, '--network-alias', service,
                       '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       *options, image, *command)
                created.append(name)
                if start:
                    docker('start', name)
                return name

            def state(name):
                return json.loads(docker('inspect', name))[0]['State']

            apps = {}
            for service in ('quacktuaries', 'bernoulli', 'athenaeum'):
                port = '8080' if service == 'athenaeum' else '8000'
                options = ['--mount', f'type=bind,src={Path(__file__).resolve()},dst=/fixture.py,readonly',
                           '--user', '10001:10001', '--stop-timeout', '3']
                if service != 'athenaeum':
                    options += ['--label', 'sablier.enable=true', '--label', f'sablier.group={prefix}-{service}',
                                '--health-cmd', "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/_health')\"",
                                '--health-interval', '1s', '--health-timeout', '1s', '--health-retries', '15']
                apps[service] = create(service, 'python:3.13-alpine', options,
                                       ['python', '/fixture.py', '--serve', port], start=service == 'athenaeum')
            sablier = create('sablier', 'sablierapp/sablier:1.18.0', [
                '--mount', 'type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock,readonly',
                '--mount', f'type=bind,src={ROOT}/deploy/sablier/sablier.yaml,dst=/etc/sablier/sablier.yaml,readonly',
                '--mount', f'type=bind,src={ROOT}/deploy/sablier/themes,dst=/etc/sablier/themes,readonly',
                '--mount', f'type=bind,src={ROOT}/design,dst=/etc/sablier/themes/assets,readonly',
            ], ['start', '--logging.level=debug', '--provider.auto-warm-externally-started=false',
                '--provider.auto-stop-on-startup=false', '--provider.auto-stop-externally-started=false',
                '--sessions.default-duration=15s', '--sessions.expiration-interval=1s'], start=True)
            wait_for(lambda: subprocess.run(['docker', 'exec', sablier, '/bin/sablier', 'health'],
                                            capture_output=True).returncode == 0)
            for entrypoint in ('Caddyfile', 'Caddyfile.local'):
                docker('run', '--rm', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--tmpfs', '/data:uid=10001,gid=10001', '--tmpfs', '/config:uid=10001,gid=10001',
                       '-e', 'SITE_DOMAIN=localhost', '-e', 'ACME_EMAIL=smoke@example.test', EDGE,
                       'caddy', 'validate', '--config', '/etc/caddy/' + entrypoint, '--adapter', 'caddyfile')
            edge = create('edge', EDGE, [
                '--publish', '127.0.0.1::8080', '-e', f'SABLIER_GROUP_PREFIX={prefix}',
                '--tmpfs', '/data:uid=10001,gid=10001', '--tmpfs', '/config:uid=10001,gid=10001',
                '--mount', f'type=bind,src={config},dst=/etc/caddy/smoke.caddy,readonly',
            ], ['caddy', 'run', '--config', '/etc/caddy/smoke.caddy', '--adapter', 'caddyfile'], start=True)
            port = docker('port', edge, '8080/tcp').rsplit(':', 1)[1]
            origin = 'http://127.0.0.1:' + port
            opener = build_opener(NoRedirect)

            def request(path, accept='application/json', body=None):
                try:
                    response = opener.open(Request(origin + path, data=body, headers={'Accept': accept}), timeout=70)
                except HTTPError as error:
                    response = error
                with response:
                    return response.code, response.headers, response.read().decode()

            def online():
                try:
                    return request('/')[0] == 200
                except (URLError, ConnectionError):
                    return False

            wait_for(online)
            for service in ('quacktuaries', 'bernoulli'):
                code, headers, _ = request('/' + service + '?source=smoke')
                assert code == 308 and headers['Location'] == '/' + service + '/?source=smoke'
                assert request('/' + service + '/_health')[0] == 404
                assert state(apps[service])['Status'] == 'created'
            assert request('/quacktuaries-other/')[0] == 200
            code, headers, html = request('/quacktuaries/deep?source=smoke', 'text/html')
            assert code in (200, 503) and 'Opening Quacktuaries.' in html, (code, html)
            assert 'data:image/svg+xml;base64,' in html and 'src="assets/' not in html
            assert 'http-equiv="refresh"' in html and '{{' not in html
            Path('/tmp/athenaeum-sablier-preview.html').write_text(html)
            assert state(apps['bernoulli'])['Status'] == 'created'
            code, _, body = request('/quacktuaries/deep?source=smoke')
            assert code == 200 and json.loads(body)['path'] == '/deep?source=smoke'
            # Sablier 1.18.0's in-memory store drops an expiring session without stopping
            # its container when the same ~1s expiry pass also discards a renewed
            # neighbour's stale timer. Production self-heals through the auto-warm scan,
            # which this test disables for daemon safety; keep the app clocks a tick apart.
            time.sleep(3)
            code, _, body = request('/bernoulli/submit?source=smoke', 'text/html', b'answer=42')
            assert code == 200 and json.loads(body) == {
                'method': 'POST', 'path': '/submit?source=smoke', 'body': 'answer=42'}
            deadline = time.monotonic() + 40
            while state(apps['quacktuaries'])['Running'] and time.monotonic() < deadline:
                assert request('/bernoulli/keep-awake')[0] == 200
                assert request('/')[0] == 200
                time.sleep(2)
            assert not state(apps['quacktuaries'])['Running'], 'Quacktuaries did not expire independently'
            assert state(apps['bernoulli'])['Running']
            assert request('/quacktuaries/_health')[0] == 404
            assert not state(apps['quacktuaries'])['Running']
            wait_for(lambda: not state(apps['bernoulli'])['Running'])
            assert state(apps['athenaeum'])['Running'] and request('/')[0] == 200
            assert state(apps['quacktuaries'])['ExitCode'] == 0
            assert state(apps['bernoulli'])['ExitCode'] == 0
            print('PASS: cold HTML/theme, cold POST, path/query, hidden health, independent expiry, always-on site')
    except BaseException:
        # Full logs: at debug level, a short tail hides the expiry window that matters most.
        for name in created:
            subprocess.run(['docker', 'logs', name], check=False)
        raise
    finally:
        for name in reversed(created):
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, check=False)
        subprocess.run(['docker', 'network', 'rm', prefix], stdout=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--serve']:
        serve()
    else:
        main()
