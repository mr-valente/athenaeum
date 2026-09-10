"""The host report: what an external monitor cannot see from Oracle's APIs.

Every quarter hour the VM overwrites one small JSON object in its backup
bucket with how full its filesystems are, whether the hourly backup is still
succeeding, and the state of each container. It signs the upload with the
instance principal the backups already use, so it opens no port and needs no
new IAM grant; the homelab monitor reads the object back with its own
read-only identity. The object lives outside the backup prefix, so the
inventory ignores it and retention never touches it.

Nothing here waits for the operation lock: status.json is written atomically,
and a report must never delay a backup or a deployment. It only probes the
lock so container states are not captured halfway through one.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import socket
import time

from .common import SERVICES, Failure, container_ok, containers_healthy, directory
from .storage import open_store

SCHEMA = 1


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if epoch else None


def filesystem(path):
    try:
        stat = os.statvfs(path)
    except OSError as error:
        return {'error': error.strerror or str(error), 'mounted': False}
    total, used = stat.f_blocks * stat.f_frsize, (stat.f_blocks - stat.f_bfree) * stat.f_frsize
    return {'total_bytes': total, 'used_bytes': used,
            'free_bytes': stat.f_bavail * stat.f_frsize,  # what unprivileged writers can still use, as df shows
            'used_percent': round(used / total * 100, 1) if total else None,
            'mounted': os.path.ismount(path)}


def uptime_seconds():
    try:
        return int(float(Path('/proc/uptime').read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def backup_status(cfg, now):
    """The same facts athenaeumctl status prints, as of the last recorded attempt."""
    path = Path(cfg['state_dir']) / 'status.json'
    status = json.loads(path.read_text()) if path.is_file() else {}
    last, attempt, drill = status.get('last_backup', {}), status.get('last_attempt', {}), status.get('last_restore_test', {})
    verified = last.get('verified_at')
    return {'verified_at': iso(verified), 'snapshot': last.get('snapshot'),
            'stale': now - (verified or 0) > cfg['stale_after_seconds'],
            'last_attempt_at': iso(attempt.get('at')), 'last_attempt_status': attempt.get('status'),
            'last_attempt_error': attempt.get('error'), 'restore_test_at': iso(drill.get('at'))}


def operation_in_progress(cfg):
    """True while a backup, restore or deployment holds the lock; never waits for it."""
    lock = directory(cfg['state_dir']) / 'operation.lock'
    if not lock.exists():
        return False
    fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def container_states(cfg):
    from .cli import stack_status  # cli imports this module; resolve the cycle at call time
    if operation_in_progress(cfg):
        return {'busy': True, 'services': None, 'ok': None}
    stack = stack_status(cfg)
    containers = stack['containers']
    # `ok` per service applies the same rule as status: an app that exited cleanly is asleep, not down.
    services = [{'service': c.get('Service'), 'state': c.get('State'), 'health': c.get('Health') or None,
                 'exit_code': c.get('ExitCode'), 'ok': container_ok(c)} for c in containers if c.get('Service') in SERVICES]
    result = {'busy': False, 'services': services, 'ok': containers_healthy(containers)}
    if 'error' in stack:
        result.update(error=stack['error'], ok=None)
    return result


def build_report(cfg, now=None):
    now = time.time() if now is None else now
    return {'schema': SCHEMA, 'hostname': socket.gethostname(),
            'generated_at': iso(now), 'uptime_seconds': uptime_seconds(),
            'filesystems': {path: filesystem(path) for path in ('/', cfg['data_root'])},
            'backup': backup_status(cfg, now), 'containers': container_states(cfg)}


def publish_report(cfg):
    name = cfg['monitor_object']
    if not name:
        raise Failure('monitor_object is null in the recovery configuration; nothing to publish')
    body = json.dumps(build_report(cfg), sort_keys=True, separators=(',', ':')).encode()
    open_store(cfg).publish(name, body)
    return {'published': name, 'bytes': len(body)}
