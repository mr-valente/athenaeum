import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


class Failure(Exception):
    """Actionable, secret-safe operator error."""


def run(args, timeout=60, **kwargs):
    try:
        return subprocess.run([str(a) for a in args], check=True, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        # Child stderr may contain config or cloud credentials. Never forward it.
        raise Failure(f'{Path(str(args[0])).name} failed or timed out; check configuration and permissions') from exc


def regular(path, private=False):
    path = Path(path)
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise Failure(f'Symlink is not allowed: {path}')
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise Failure(f'Required regular file is missing: {path}')
    if private and path.stat().st_mode & 0o077:
        raise Failure(f'File must be private (mode 0600): {path}')
    return path


def directory(path):
    path = Path(path)
    if not path.is_dir() or any(p.is_symlink() for p in [path, *path.parents]):
        raise Failure(f'Required real directory is missing: {path}')
    return path


def digest(path):
    with open(path, 'rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def atomic_json(path, data):
    path = Path(path)
    directory(path.parent)
    fd, temp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(data, file, sort_keys=True)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


# Registered stateful applications. Each owns one SQLite database at
# apps/<name>/data/app.db and one persistent signing key named by the config
# key below. Its Compose service, secret (<name>_session), image override
# (<NAME>_IMAGE) and public prefix all derive from the name.
APPS = {
    'quacktuaries': {'secret': 'session_secret', 'root_path': '/quacktuaries',
                     'tables': {'teachers', 'sessions', 'players', 'device_stats', 'events'}},
    'bernoulli': {'secret': 'bernoulli_session_secret', 'root_path': '/bernoulli',
                  'tables': {'teachers', 'sessions', 'players', 'flip_flop_rounds', 'flip_flop_votes'}},
}
SERVICES = ('edge', 'athenaeum', 'sablier', *APPS)


def containers_healthy(containers, allow_sleeping=True):
    if len(containers) != len(SERVICES) or {item.get('Service') for item in containers} != set(SERVICES):
        return False
    return all(
        (item.get('State') == 'running' and item.get('Health') == 'healthy')
        or (allow_sleeping and item.get('Service') in APPS
            and item.get('State') == 'exited' and item.get('ExitCode') == 0)
        for item in containers
    )


def secret_path(cfg, app):
    return Path(cfg[APPS[app]['secret']])


def data_dir(cfg, app):
    return Path(cfg['data_root']) / 'apps' / app / 'data'


# Ordinary host setup only supplies the disk UUID, public backup key and bucket.
DEFAULTS = {
    'schema': 1, 'mode': 'production', 'data_root': '/srv/athenaeum',
    'state_dir': '/var/lib/athenaeum', 'compose_project': 'athenaeum',
    'compose_files': ['/opt/athenaeum/stack/compose.yaml'],
    'compose_env': '/etc/athenaeum/compose.env',
    'session_secret': '/etc/athenaeum/quacktuaries-session-secret',
    'bernoulli_session_secret': '/etc/athenaeum/bernoulli-session-secret',
    'age_binary': '/opt/athenaeum/tools/age', 'bucket_cap_bytes': 8_000_000_000,
    'max_snapshot_bytes': 1_000_000_000, 'max_restore_bytes': 4_000_000_000,
    'min_free_bytes': 2_000_000_000, 'stale_after_seconds': 7200,
}


def load_config(path):
    source = regular(path, private=True).absolute()
    try:
        cfg = DEFAULTS | json.loads(source.read_text())
        cfg['store'] = {'kind': 'oci', 'prefix': 'athenaeum/v1/'} | cfg['store']
        secrets = {app['secret'] for app in APPS.values()}
        expected = {'schema', 'mode', 'data_root', 'filesystem_uuid', 'state_dir', 'compose_project',
                    'compose_files', 'compose_env', 'recipient', 'age_binary',
                    'bucket_cap_bytes', 'max_snapshot_bytes', 'max_restore_bytes', 'min_free_bytes',
                    'stale_after_seconds', 'store'} | secrets
        if set(cfg) != expected or cfg['schema'] != 1 or cfg['mode'] not in ('production', 'local'):
            raise ValueError()
        for key in ('data_root', 'state_dir', 'compose_env', 'age_binary', *sorted(secrets)):
            if (not isinstance(cfg[key], str) or not Path(cfg[key]).is_absolute()
                    or '..' in Path(cfg[key]).parts or any(c in cfg[key] for c in '\n\r\x00')):
                raise ValueError()
        if not cfg['compose_files'] or not all(Path(p).is_absolute() for p in cfg['compose_files']):
            raise ValueError()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', cfg['compose_project']):
            raise ValueError()
        if not re.fullmatch(r'age1[0-9a-z]{58}', cfg['recipient']):
            raise ValueError()
        for key in ('bucket_cap_bytes', 'max_snapshot_bytes', 'max_restore_bytes', 'min_free_bytes', 'stale_after_seconds'):
            if type(cfg[key]) is not int or cfg[key] <= 0:
                raise ValueError()
        if cfg['max_snapshot_bytes'] > min(cfg['bucket_cap_bytes'], 5_000_000_000):
            raise ValueError()
        store = cfg['store']
        if store['kind'] == 'oci':
            if set(store) != {'kind', 'region', 'namespace', 'bucket', 'prefix'}:
                raise ValueError()
            if not all(re.fullmatch(r'[a-zA-Z0-9._-]+', store[k]) for k in ('region', 'namespace', 'bucket')):
                raise ValueError()
        elif store['kind'] == 'local':
            if set(store) != {'kind', 'directory', 'prefix'} or cfg['mode'] != 'local' or not Path(store['directory']).is_absolute():
                raise ValueError()
        else:
            raise ValueError()
        if not re.fullmatch(r'(?:[a-z0-9][a-z0-9_-]*/)+', store['prefix']):
            raise ValueError()
        if cfg['mode'] == 'production':
            if store['kind'] != 'oci' or not re.fullmatch(r'[a-fA-F0-9-]{8,64}', cfg['filesystem_uuid']):
                raise ValueError()
        elif store['kind'] != 'local':
            raise ValueError()
        if Path(cfg['data_root']) == Path('/') or Path(cfg['state_dir']).is_relative_to(cfg['data_root']):
            raise ValueError()
        if len({cfg[key] for key in secrets}) != len(secrets):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise Failure('Invalid recovery configuration; use deploy/recovery.example.json and replace every placeholder') from None
    cfg['_source'] = str(source)
    return cfg


def guard_mount(cfg):
    root = directory(cfg['data_root'])
    if cfg['mode'] == 'local':
        return {'mode': 'local', 'mount_verified': False}
    raw = run(['findmnt', '--json', '--mountpoint', root, '--output', 'TARGET,UUID,FSTYPE,OPTIONS'])
    try:
        filesystems = json.loads(raw)['filesystems']
        if len(filesystems) != 1:
            raise ValueError()
        fs = filesystems[0]
        if (fs['target'] != str(root) or fs['uuid'] != cfg['filesystem_uuid']
                or fs['fstype'] != 'ext4' or 'rw' not in fs['options'].split(',')):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise Failure('Expected writable ext4 data filesystem UUID is not mounted; refusing operation') from None
    return {'mode': 'production', 'mount_verified': True}


def preflight(cfg):
    result = guard_mount(cfg)
    root = Path(cfg['data_root'])
    for child in [*(data_dir(cfg, app) for app in APPS), *(root / r for r in ('edge/data', 'edge/config', 'backups/staging'))]:
        directory(child)
        if child.stat().st_dev != root.stat().st_dev:
            raise Failure(f'Unexpected nested filesystem: {child}')
    for app in APPS:
        regular(secret_path(cfg, app), private=True)
        if len(secret_path(cfg, app).read_text().strip()) < 32:
            raise Failure(f'Session signing key for {app} is missing or too short')
    regular(cfg['compose_env'], private=True)
    for path in cfg['compose_files']:
        regular(path)
    free = shutil.disk_usage(root).free
    if free < cfg['min_free_bytes']:
        raise Failure('Insufficient free space on the data filesystem')
    return {**result, 'free_bytes': free}


@contextlib.contextmanager
def operation_lock(cfg):
    state = directory(cfg['state_dir'])
    if state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077:
        raise Failure('Operations state directory must be private and owned by the operator')
    lock = state / 'operation.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure('Another backup, restore, or deployment operation holds the host lock') from None
        yield
    finally:
        os.close(fd)


def compose_files(cfg):
    return list(cfg['compose_files'])


def compose_invocation(cfg, *args):
    """Build one authoritative command for captured and interactive Compose use."""
    command = ['docker', 'compose', '--project-name', cfg['compose_project'], '--env-file', cfg['compose_env']]
    for file in compose_files(cfg):
        command += ['-f', file]
    # Explicit config is authoritative, not inherited interactive shell variables.
    private = {f'{app.upper()}_{suffix}' for app in APPS for suffix in ('SECRET_FILE', 'IMAGE')}
    env = {k: v for k, v in os.environ.items() if k not in private | {
        'DATA_ROOT', 'EDGE_IMAGE', 'ATHENAEUM_IMAGE',
        'RUNTIME_UID', 'RUNTIME_GID', 'SITE_DOMAIN', 'ACME_EMAIL',
        'LOCAL_HTTP_PORT', 'LOCAL_HTTPS_PORT', 'COMPOSE_FILE', 'COMPOSE_PROJECT_NAME',
        'COMPOSE_PROFILES', 'COMPOSE_ENV_FILES'}}
    return command + list(args), env


def compose(cfg, *args):
    command, env = compose_invocation(cfg, *args)
    return run(command, timeout=180, env=env)


def compose_visible(cfg, *args):
    """Show requested Docker progress/logs; long pulls and log following can finish."""
    command, env = compose_invocation(cfg, *args)
    try:
        subprocess.run(command, env=env, check=True)
    except (subprocess.CalledProcessError, OSError):
        raise Failure('Compose failed; inspect the output above. No later step was run.') from None


def validate_compose(cfg):
    model = json.loads(compose(cfg, 'config', '--format', 'json'))
    root = Path(cfg['data_root'])
    expected = {'edge': {'/data': root / 'edge/data', '/config': root / 'edge/config'}}
    expected.update({app: {'/data': data_dir(cfg, app)} for app in APPS})
    for service, mounts in expected.items():
        actual = {v['target']: v for v in model['services'][service]['volumes']}
        for dest, source in mounts.items():
            mount = actual[dest]
            if mount.get('type') != 'bind' or mount['source'] != str(source) or mount.get('bind', {}).get('create_host_path', True):
                raise Failure('Compose data mounts do not match recovery configuration')
    for app in APPS:
        if model['secrets'][app + '_session']['file'] != str(secret_path(cfg, app)):
            raise Failure(f'Compose signing key for {app} does not match recovery configuration')
    return model


def deployment(cfg):
    validate_compose(cfg)
    images = {}
    for service in SERVICES:
        ids = compose(cfg, 'ps', '--all', '--quiet', service).decode().split()
        if len(ids) > 1:
            raise Failure(f'Expected at most one existing {service} container for snapshot metadata')
        if not ids:
            # A newly registered service has no container until its first
            # deployment; the backup before that deployment records nothing for it.
            continue
        container = json.loads(run(['docker', 'inspect', ids[0]]))[0]
        image = json.loads(run(['docker', 'image', 'inspect', container['Image']]))[0]
        images[service] = {'id': image['Id'], 'reference': container['Config']['Image'],
                           'digests': image.get('RepoDigests', []), 'architecture': image['Architecture']}
    return images
