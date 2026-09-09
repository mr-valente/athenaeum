import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

from .common import Failure, atomic_json, compose, directory, guard_mount, load_config, operation_lock, preflight, validate_compose, run
from .recovery import backup, cleanup_staging, export_snapshot, load_commits, restore
from .runtime import test_runtime
from .storage import open_store
from .delivery import Updater, delivery_status, pause
from .releases import load_updates, SERVICES


def write_status(cfg, key, value):
    path = Path(cfg['state_dir']) / 'status.json'
    data = json.loads(path.read_text()) if path.is_file() else {}
    data[key] = value
    atomic_json(path, data)


def print_status(result):
    delivery = result['delivery']
    releases = delivery.get('releases', {})
    print('Updates: ' + ('paused' if delivery.get('paused') else 'allowed'))
    backup = result.get('last_backup', {})
    when = backup.get('verified_at')
    stamp = datetime.fromtimestamp(when, timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if when else 'none yet'
    print('Backup: ' + stamp + (' (STALE)' if result['backup_stale'] else ''))
    storage = result['storage']
    print('Data disk: ' + (storage['error'] if 'error' in storage else f"{storage['free_bytes'] / 1e9:.1f} GB free"))
    containers = {c['Service']: c for c in delivery.get('containers', [])}
    for name in SERVICES:
        c = containers.get(name, {})
        print(f"{name}: {c.get('State', 'missing')} / {c.get('Health', 'unknown')}")
    for name, outcome in releases.get('last_poll', {}).get('results', {}).items():
        if outcome != 'current':
            print(f'Update {name}: {outcome}')
    for name, values in delivery.get('timers', {}).items():
        print(name + ': ' + ' / '.join(values))
    if releases.get('transaction'):
        print('ACTION: interrupted deployment; inspect --json and use compatible rollback')
    errors = [delivery.get('error'), releases.get('last_error', {}).get('error'),
              result.get('last_attempt', {}).get('error')]
    errors += [entry.get('error') for entry in delivery.get('discovery', {}).values()]
    for error in dict.fromkeys(e for e in errors if e):
        print('ERROR: ' + error)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Athenaeum host persistence and recovery')
    parser.add_argument('--config', default='/etc/athenaeum/recovery.json')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('guard-mount', 'preflight', 'start', 'backup', 'list', 'cleanup-staging', 'pause-updates', 'resume-updates'):
        sub.add_parser(command)
    status = sub.add_parser('status')
    status.add_argument('--json', action='store_true', help='Full diagnostic details')
    deploy = sub.add_parser('deploy')
    selection = deploy.add_mutually_exclusive_group()
    selection.add_argument('--initial', action='store_true')
    selection.add_argument('--app', choices=SERVICES)
    rollback = sub.add_parser('rollback')
    rollback.add_argument('app', choices=SERVICES)
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
                if args.command in ('pause-updates', 'resume-updates'):
                    result = pause(cfg, args.command == 'pause-updates')
                elif args.command in ('deploy', 'rollback'):
                    updater = Updater(cfg, load_updates(cfg))
                    result = updater.run(args.initial, args.app) if args.command == 'deploy' else updater.rollback(args.app)
                elif args.command == 'preflight':
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
                    write_status(cfg, 'last_attempt', {'at': time.time(), 'status': 'running'})
                    try:
                        result = backup(cfg)
                    except BaseException as error:
                        message = str(error) if isinstance(error, Failure) else type(error).__name__ + ': backup failed'
                        write_status(cfg, 'last_attempt', {'at': time.time(), 'status': 'failed', 'error': message})
                        raise
                    write_status(cfg, 'last_backup', result)
                    write_status(cfg, 'last_attempt', {'at': time.time(), 'status': 'ok'})
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
                    result['delivery'] = delivery_status(cfg)
                    if args.json:
                        print(json.dumps(result, sort_keys=True))
                    else:
                        print_status(result)
                    delivery = result['delivery']
                    releases = delivery.get('releases', {})
                    unhealthy = {c.get('Service') for c in delivery.get('containers', [])} != set(SERVICES) or any(c.get('State') != 'running' or c.get('Health') != 'healthy' for c in delivery.get('containers', []))
                    return int(bool(result['backup_stale'] or 'error' in result['storage'] or result.get('last_attempt', {}).get('status') != 'ok'
                        or 'error' in delivery or releases.get('transaction') or releases.get('last_error') or unhealthy
                        or any(e.get('error') for e in delivery.get('discovery', {}).values())
                        or 'failed-digest-held' in releases.get('last_poll', {}).get('results', {}).values()))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, Failure) else f'{type(error).__name__}: operation failed; inspect configuration, permissions and provider access'
        print('athenaeumctl: ' + message, file=sys.stderr)
        return 1
