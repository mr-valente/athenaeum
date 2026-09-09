import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

from .common import Failure, atomic_json, compose, guard_mount, load_config, operation_lock, preflight, validate_compose, run
from .recovery import backup, cleanup_staging, export_snapshot, load_commits, restore
from .runtime import test_runtime
from .storage import open_store

SERVICES = ('edge', 'athenaeum', 'quacktuaries')


def write_status(cfg, key, value):
    path = Path(cfg['state_dir']) / 'status.json'
    data = json.loads(path.read_text()) if path.is_file() else {}
    data[key] = value
    atomic_json(path, data)


def recorded_backup(cfg):
    """Run under operation_lock; both CLI and manual updates report the same status."""
    write_status(cfg, 'last_attempt', {'at': time.time(), 'status': 'running'})
    try:
        result = backup(cfg)
    except BaseException as error:
        message = str(error) if isinstance(error, Failure) else type(error).__name__ + ': backup failed'
        write_status(cfg, 'last_attempt', {'at': time.time(), 'status': 'failed', 'error': message})
        raise
    write_status(cfg, 'last_backup', result)
    write_status(cfg, 'last_attempt', {'at': time.time(), 'status': 'ok'})
    return result


def stack_status(cfg):
    try:
        raw = compose(cfg, 'ps', '--all', '--format', 'json').decode().strip()
        containers = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
        return {'containers': containers}
    except (Failure, ValueError) as error:
        return {'containers': [], 'error': str(error) if isinstance(error, Failure) else 'Invalid Compose status output'}


def print_status(result):
    backup = result.get('last_backup', {})
    when = backup.get('verified_at')
    stamp = datetime.fromtimestamp(when, timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if when else 'none yet'
    print('Backup: ' + stamp + (' (STALE)' if result['backup_stale'] else ''))
    storage = result['storage']
    print('Data disk: ' + (storage['error'] if 'error' in storage else f"{storage['free_bytes'] / 1e9:.1f} GB free"))
    containers = {c['Service']: c for c in result['stack']['containers']}
    for name in SERVICES:
        c = containers.get(name, {})
        print(f"{name}: {c.get('State', 'missing')} / {c.get('Health', 'unknown')}")
    for error in (result['stack'].get('error'), result.get('last_attempt', {}).get('error')):
        if error:
            print('ERROR: ' + error)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Athenaeum host persistence and recovery')
    parser.add_argument('--config', default='/etc/athenaeum/recovery.json')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('guard-mount', 'preflight', 'start', 'backup', 'list', 'cleanup-staging'):
        sub.add_parser(command)
    status = sub.add_parser('status')
    status.add_argument('--json', action='store_true', help='Full diagnostic details')
    export = sub.add_parser('export')
    export.add_argument('--snapshot', required=True)
    export.add_argument('--output', required=True)
    for command in ('restore', 'restore-test'):
        p = sub.add_parser(command)
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument('--snapshot')
        source.add_argument('--archive')
        p.add_argument('--sha256')
        p.add_argument('--target', required=True)
        p.add_argument('--identity', required=True)
        if command == 'restore-test':
            p.add_argument('--image', required=True)
    args = parser.parse_args()
    cfg = None
    def terminated(signum, frame):
        raise Failure('Operation interrupted; temporary plaintext will be removed')
    signal.signal(signal.SIGTERM, terminated)
    try:
        cfg = load_config(args.config)
        if args.command == 'guard-mount':
            result = guard_mount(cfg)  # Must work before Docker starts; no Docker or cloud call.
        else:
            with operation_lock(cfg):
                if args.command == 'preflight':
                    result = preflight(cfg)
                elif args.command == 'start':
                    preflight(cfg)
                    validate_compose(cfg)
                    if cfg['mode'] == 'production':
                        run(['systemctl', 'is-active', '--quiet', 'athenaeum-metadata-guard.service'])
                    compose(cfg, 'config', '--quiet')
                    compose(cfg, 'up', '--detach', '--wait', '--wait-timeout', '120', '--no-build', '--pull', 'never')
                    result = {'started': True}
                elif args.command == 'backup':
                    result = recorded_backup(cfg)
                elif args.command == 'cleanup-staging':
                    cleanup_staging(cfg)
                    result = {'staging_cleaned': True}
                elif args.command == 'list':
                    store = open_store(cfg)
                    objects, multipart = store.inventory()
                    with tempfile.TemporaryDirectory() as temp:
                        points = load_commits(store, objects, cfg['store']['prefix'], Path(temp))
                    result = {'snapshots': [{k: c[k] for k in ('id', 'created_at', 'bytes', 'sha256')} for c in points],
                              'bucket_bytes': sum(o['size'] for o in objects), 'multipart_bytes': multipart}
                elif args.command == 'export':
                    result = export_snapshot(cfg, args.snapshot, args.output)
                elif args.command in ('restore', 'restore-test'):
                    manifest = restore(cfg, args.target, args.identity, args.snapshot, args.archive, args.sha256)
                    result = {'restored': str(Path(args.target).absolute()), 'database': manifest['database']}
                    if args.command == 'restore-test':
                        result.update(test_runtime(args.target, args.image, manifest))
                        write_status(cfg, 'last_restore_test', {'at': time.time(), 'status': 'ok'})
                else:
                    path = Path(cfg['state_dir']) / 'status.json'
                    result = json.loads(path.read_text()) if path.is_file() else {}
                    result['backup_stale'] = time.time() - result.get('last_backup', {}).get('verified_at', 0) > cfg['stale_after_seconds']
                    try:
                        result['storage'] = preflight(cfg)
                    except Failure as error:
                        result['storage'] = {'error': str(error)}
                    result['stack'] = stack_status(cfg)
                    if args.json:
                        print(json.dumps(result, sort_keys=True))
                    else:
                        print_status(result)
                    stack = result['stack']
                    unhealthy = {c.get('Service') for c in stack['containers']} != set(SERVICES) or any(
                        c.get('State') != 'running' or c.get('Health') != 'healthy' for c in stack['containers'])
                    return int(bool(result['backup_stale'] or 'error' in result['storage']
                                    or result.get('last_attempt', {}).get('status') != 'ok'
                                    or 'error' in stack or unhealthy))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, Failure) else f'{type(error).__name__}: operation failed; inspect configuration, permissions and provider access'
        print('athenaeumctl: ' + message, file=sys.stderr)
        return 1
