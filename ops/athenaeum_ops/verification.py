"""Public HTTPS, container health, and Oracle metadata isolation checks."""
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urljoin
from .common import APPS, preflight, validate_compose, compose, containers_healthy, run, Failure
from .cli import stack_status


def say(message):
    print(message, flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Following a redirect would hide a wrong Location or status code.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(opener, url, status, location=None, timeout=20):
    try:
        response = opener.open(url, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        actual_location = urljoin(url, response.headers.get('Location', ''))
        if response.code != status or (location and actual_location != location):
            raise Failure(f'HTTPS check failed for {url}: expected {status}' + (f' and Location {location}' if location else ''))
    say(f'PASS: {status} {url}')


def verify(cfg):
    preflight(cfg)
    model = validate_compose(cfg)
    status = stack_status(cfg)
    if not containers_healthy(status['containers']):
        raise Failure('Expected healthy infrastructure and healthy or cleanly stopped apps; inspect logs.')
    domain = model['services']['edge']['environment']['SITE_DOMAIN']
    origin = 'https://' + domain
    opener = urllib.request.build_opener(NoRedirect)
    probe(opener, origin + '/', 200)
    probe(opener, 'https://www.' + domain + '/', 308, origin + '/')
    say('Checking app routes wakes sleeping apps and renews their idle sessions.')
    for app in APPS.values():
        prefix = origin + app['root_path']
        probe(opener, prefix + '/', 200, timeout=70)
        probe(opener, prefix + '?source=setup', 308, prefix + '/?source=setup')
        probe(opener, prefix + '/_health', 404)
    if not containers_healthy(stack_status(cfg)['containers'], allow_sleeping=False):
        raise Failure('Apps did not become healthy after their wake-up probes; inspect logs.')
    # First prove wget itself works in the right container. A missing command or
    # container must never be mistaken for successful metadata isolation.
    edge = compose(cfg, 'ps', '--quiet', 'edge').decode().strip()
    run(['docker', 'exec', edge, 'wget', '-q', '-O', '/dev/null', 'http://127.0.0.1:8081/_health'])
    run(['iptables', '-C', 'DOCKER-USER', '-d', '169.254.169.254/32', '-m', 'comment',
         '--comment', 'athenaeum-block-imds', '-j', 'REJECT'])
    # BusyBox wget emits HTTP status lines even for 401/403. Any response means
    # metadata is reachable. The response body always goes to /dev/null.
    import subprocess
    result = subprocess.run(['docker', 'exec', edge, 'wget', '-S', '-T', '3', '-O', '/dev/null',
                             '--header=Authorization: Bearer Oracle', 'http://169.254.169.254/opc/v2/instance/'],
                            capture_output=True, timeout=10)
    stderr = result.stderr.decode(errors='replace')
    if result.returncode == 0 or 'HTTP/' in stderr or not any(text in stderr.lower() for text in
            ('connection refused', 'timed out', 'network is unreachable', 'no route to host')):
        raise Failure('Metadata denial not proven. Inspect the Docker metadata guard; no response body was retained.')
    say('PASS: container metadata access denied. Finish the browser classroom check in the runbook.')
