#!/usr/bin/env python3
"""Build and smoke-test native images; publish a verified multi-platform tag."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=False, timeout=900):
    return subprocess.run(list(map(str, args)), check=True, cwd=ROOT, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None).stdout


def smoke(service, image):
    name = 'athenaeum-ci-' + uuid.uuid4().hex[:12]
    args = ['docker', 'run', '--detach', '--name', name, '--pull', 'never', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '512m',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m', '--tmpfs', '/data:rw,nosuid,nodev,size=32m,uid=10001,gid=10001',
            '--tmpfs', '/config:rw,nosuid,nodev,size=32m,uid=10001,gid=10001']
    if service == 'quacktuaries':
        args += ['--env', 'APP_ENV=production', '--env', 'ROOT_PATH=/quacktuaries',
                 '--env', 'SESSION_SECRET=synthetic-ci-only-' + 'x' * 40]
    elif service == 'edge':
        args += ['--env', 'SITE_DOMAIN=localhost', '--env', 'ACME_EMAIL=fixture@example.invalid', '--env', 'LOCAL_HTTPS_PORT=8443']
    args += [image]
    if service == 'edge':
        args += ['caddy', 'run', '--config', '/etc/caddy/Caddyfile.local', '--adapter', 'caddyfile']
    try:
        run(*args, capture=True)
        for _ in range(60):
            state = json.loads(run('docker', 'inspect', name, capture=True))[0]['State']
            if state.get('Health', {}).get('Status') == 'healthy':
                if service == 'quacktuaries':
                    status = json.loads(run('docker', 'exec', name, 'python', '-m', 'app.operations', 'drain', capture=True))
                    assert status['held'] and not status['active_class']
                    run('docker', 'exec', name, 'python', '-m', 'app.operations', 'release')
                return
            if not state['Running']:
                raise RuntimeError('Native container exited')
            time.sleep(1)
        raise RuntimeError('Native container health deadline exceeded')
    finally:
        run('docker', 'rm', '--force', name, capture=True)


def check_operations():
    spec = {'x86_64': ('amd64', 'cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10'),
            'aarch64': ('arm64', '6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4')}
    target, expected = spec[platform.machine()]
    import tarfile
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        with urllib.request.urlopen(f'https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-{target}.tar.gz', timeout=30) as source:
            raw = source.read(20_000_000)
        assert hashlib.sha256(raw).hexdigest() == expected, 'age checksum mismatch'
        archive = work / 'age.tgz'
        archive.write_bytes(raw)
        with tarfile.open(archive) as tar:
            for binary in ('age', 'age-keygen'):
                dest = work / binary
                dest.write_bytes(tar.extractfile('age/' + binary).read())
                dest.chmod(0o700)
        env = dict(os.environ, ATHENAEUM_TEST_AGE=str(work / 'age'))
        subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'],
                       cwd=ROOT, env=env, check=True, timeout=300)


def env(name, pattern):
    value = os.environ[name]
    if not re.fullmatch(pattern, value):
        raise ValueError('Invalid CI input: ' + name)
    return value


def metadata():
    commit = env('GITHUB_SHA', r'[a-f0-9]{40}')
    repo = env('GITHUB_REPOSITORY', r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
    owner = env('DOCKERHUB_USERNAME', r'[a-z0-9][a-z0-9_-]*')
    number = env('GITHUB_RUN_NUMBER', r'[0-9]+')
    attempt = env('GITHUB_RUN_ATTEMPT', r'[0-9]+')
    return commit, repo, owner, f'build-{number}-{commit[:12]}-{attempt}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'build', 'push-platform', 'publish'])
    args = parser.parse_args()
    contract = json.loads((ROOT / 'ci/images.json').read_text())['services']
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
    if args.command in ('check', 'build'):
        for service, c in contract.items():
            image = 'athenaeum-ci-' + service + ':local'
            run('docker', 'buildx', 'build', '--load', '--platform', 'linux/' + arch, '--file', c['dockerfile'], '--tag', image, '.')
            smoke(service, image)
            if service == 'quacktuaries':
                run('docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
                    '--mount', f'type=bind,src={ROOT / "tests"},dst=/tests,readonly', image,
                    'python', '-m', 'unittest', 'discover', '-s', '/tests', '-p', 'test_deployment.py', '-v')
                run('python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_publication.py', '-v')
        if args.command == 'check' and 'athenaeum' in contract:
            check_operations()
        return
    commit, repository, owner, version = metadata()
    if args.command == 'push-platform':
        for service, c in contract.items():
            tag = f'docker.io/{owner}/{c["image_name"]}:{version}-{arch}'
            run('docker', 'tag', 'athenaeum-ci-' + service + ':local', tag)
            run('docker', 'push', tag)
        return
    ready = []
    for service, c in contract.items():
        image = f'docker.io/{owner}/{c["image_name"]}'
        sources = []
        for target in ('amd64', 'arm64'):
            data = json.loads(run('docker', 'buildx', 'imagetools', 'inspect', f'{image}:{version}-{target}',
                                  '--format', '{{json .Manifest}}', capture=True))
            assert re.fullmatch(r'sha256:[a-f0-9]{64}', data['digest'])
            sources.append(image + '@' + data['digest'])
        annotations = {
            'org.opencontainers.image.source': 'https://github.com/' + repository,
            'org.opencontainers.image.revision': commit,
            'org.opencontainers.image.version': version,
            'org.opencontainers.image.created': datetime.now(timezone.utc).isoformat(),
            'io.valentemath.style': c['style_version'], 'io.valentemath.schema': str(c['schema_version']),
        }
        options = [arg for key, value in annotations.items() for arg in ('--annotation', 'index:' + key + '=' + value)]
        run('docker', 'buildx', 'imagetools', 'create', '--tag', image + ':' + version, *options, *sources)
        data = json.loads(run('docker', 'buildx', 'imagetools', 'inspect', image + ':' + version,
                              '--format', '{{json .Manifest}}', capture=True))
        platforms = {(m.get('platform', {}).get('os'), m.get('platform', {}).get('architecture')) for m in data['manifests']}
        assert {('linux', 'amd64'), ('linux', 'arm64')} <= platforms, 'Both native manifests required'
        assert re.fullmatch(r'sha256:[a-f0-9]{64}', data['digest'])
        ready.append((image, data['digest']))
    # Publish :latest only after all builds/tests/index checks succeed.
    for image, digest in ready:
        run('docker', 'buildx', 'imagetools', 'create', '--tag', image + ':latest',
            '--tag', image + ':sha-' + commit, image + '@' + digest)


if __name__ == '__main__':
    main()
