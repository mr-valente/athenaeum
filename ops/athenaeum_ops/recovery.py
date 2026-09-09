from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from .archive import create_archive, unpack_verified
from .common import Failure, atomic_json, deployment, digest, directory, guard_mount, preflight, regular, run
from .storage import open_store

ID = r'\d{8}T\d{6}Z-[a-f0-9]{32}'


def new_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex


def cleanup_staging(cfg):
    guard_mount(cfg)
    staging = directory(Path(cfg['data_root']) / 'backups/staging')
    for entry in staging.iterdir():
        if (re.fullmatch(r'snapshot-[a-z0-9_]{8}', entry.name) and not entry.is_symlink()
                and entry.is_dir() and (entry / '.athenaeum-staging').is_file()):
            if entry.stat().st_uid != os.geteuid() or entry.stat().st_mode & 0o077:
                raise Failure('Unexpected staging owner/permissions; inspect it manually')
            shutil.rmtree(entry)


def load_commits(store, objects, prefix, temp):
    by_name = {item['name']: item for item in objects}
    commits = []
    for item in objects:
        if not re.fullmatch(re.escape(prefix) + 'commits/' + ID + r'\.json', item['name']):
            continue
        if item['size'] > 4096:
            continue
        path = temp / ('commit-' + uuid.uuid4().hex)
        try:
            store.get(item['name'], path, 4096, item['etag'])
            commit = json.loads(path.read_text())
            name = item['name'].split('/')[-1][:-5]
            expected = prefix + 'snapshots/' + name + '.tar.gz.age'
            if (set(commit) != {'schema', 'id', 'created_at', 'object', 'bytes', 'sha256'} or commit['schema'] != 1
                    or commit['id'] != name or commit['object'] != expected
                    or not re.fullmatch(r'[a-f0-9]{64}', commit['sha256'])
                    or type(commit['bytes']) is not int or commit['bytes'] <= 0):
                continue
            stamp = datetime.fromisoformat(commit['created_at'])
            if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                continue
            payload = by_name.get(expected)
            if payload is None or payload['size'] != commit['bytes']:
                continue
            commits.append({**commit, '_commit': item, '_payload': payload})
        except (ValueError, KeyError, TypeError):
            # Unknown objects remain untouched and count toward capacity.
            continue
        finally:
            path.unlink(missing_ok=True)
    return sorted(commits, key=lambda c: (c['created_at'], c['id']), reverse=True)


def retention_keep(commits):
    """Latest point in each of the newest 24 UTC hours and 7 UTC days."""
    hours, days, keep = set(), set(), set()
    for commit in commits:
        date = datetime.fromisoformat(commit['created_at']).astimezone(timezone.utc)
        hour, day = date.strftime('%Y%m%d%H'), date.strftime('%Y%m%d')
        selected = False
        if hour not in hours and len(hours) < 24:
            hours.add(hour)
            selected = True
        if day not in days and len(days) < 7:
            days.add(day)
            selected = True
        if selected:
            keep.add(commit['id'])
    if commits:
        keep.add(commits[0]['id'])
    return keep


def backup(cfg, store=None, images=None):
    preflight(cfg)
    cleanup_staging(cfg)
    images = deployment(cfg) if images is None else images
    store = open_store(cfg) if store is None else store
    staging_root = Path(cfg['data_root']) / 'backups/staging'
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=staging_root) as temp:
        temp = Path(temp)
        (temp / '.athenaeum-staging').touch(mode=0o600)
        encrypted = create_archive(cfg, temp, images)
        identifier = new_id()
        prefix = cfg['store']['prefix']
        object_name = prefix + 'snapshots/' + identifier + '.tar.gz.age'
        commit_name = prefix + 'commits/' + identifier + '.json'
        commit = {'schema': 1, 'id': identifier, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'object': object_name, 'bytes': encrypted.stat().st_size, 'sha256': digest(encrypted)}
        commit_file = temp / 'commit.json'
        atomic_json(commit_file, commit)
        objects, multipart = store.inventory()
        required = sum(o['size'] for o in objects) + multipart + commit['bytes'] + commit_file.stat().st_size
        if required > cfg['bucket_cap_bytes']:
            raise Failure('Bucket capacity would exceed the configured cap; previous recovery points retained')
        guard_mount(cfg)
        store.put(object_name, encrypted)
        verification = temp / 'verification.age'
        store.get(object_name, verification, cfg['max_snapshot_bytes'])
        if verification.stat().st_size != commit['bytes'] or digest(verification) != commit['sha256']:
            raise Failure('Uploaded ciphertext verification failed; no commit written or old backup pruned')
        guard_mount(cfg)
        # Publication is the commit object, written only after readback verification.
        store.put(commit_name, commit_file)
        verified_commit = temp / 'verified-commit.json'
        store.get(commit_name, verified_commit, 4096)
        if verified_commit.read_bytes() != commit_file.read_bytes():
            raise Failure('Commit readback verification failed; old recovery points retained')
        objects, multipart = store.inventory()
        commits = load_commits(store, objects, prefix, temp)
        if identifier not in {c['id'] for c in commits}:
            raise Failure('New committed snapshot not visible; refusing retention')
        keep = retention_keep(commits)
        pruned = 0
        for old in commits:
            if old['id'] not in keep:
                guard_mount(cfg)
                # Invalidate the old commit first. An interrupted deletion can
                # leave ciphertext, never a seemingly valid missing payload.
                store.delete(old['_commit']['name'], old['_commit']['etag'])
                store.delete(old['_payload']['name'], old['_payload']['etag'])
                pruned += 1
        return {'snapshot': identifier, 'object': object_name, 'bytes': commit['bytes'],
                'ciphertext_sha256': commit['sha256'], 'verified_at': time.time(), 'pruned': pruned}


def select_commit(cfg, store, temp, identifier):
    if not re.fullmatch(ID, identifier):
        raise Failure('Supply the explicit snapshot ID returned by backup or list')
    objects, _ = store.inventory()
    commits = load_commits(store, objects, cfg['store']['prefix'], temp)
    return next((c for c in commits if c['id'] == identifier), None)


def export_snapshot(cfg, identifier, destination, store=None):
    """Download a verified encrypted external copy; no private identity needed."""
    destination = Path(destination).absolute()
    directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise Failure('Export destination already exists')
    store = open_store(cfg) if store is None else store
    with tempfile.TemporaryDirectory(prefix='.athenaeum-export-', dir=destination.parent) as temp:
        temp = Path(temp)
        commit = select_commit(cfg, store, temp, identifier)
        if not commit:
            raise Failure('Snapshot is missing or not committed')
        encrypted = temp / 'snapshot.age'
        store.get(commit['object'], encrypted, cfg['max_snapshot_bytes'], commit['_payload']['etag'])
        if digest(encrypted) != commit['sha256'] or encrypted.stat().st_size != commit['bytes']:
            raise Failure('Encrypted download failed checksum verification')
        # link is exclusive; never overwrite an existing output even after a race.
        os.chmod(encrypted, 0o600)
        os.link(encrypted, destination)
    return {'file': str(destination), 'ciphertext_sha256': commit['sha256']}


def restore(cfg, target, identity, identifier=None, archive=None, expected_sha256=None, store=None):
    """Can run on a replacement host: does not require the old data mount."""
    target = Path(target).absolute()
    directory(target.parent)
    source_root = Path(cfg['data_root']).absolute()
    if target == source_root or source_root in target.parents or target in source_root.parents:
        raise Failure('Restore target overlaps the live data root')
    if target.exists() or target.is_symlink():
        raise Failure('Restore target must be a new directory, never a live overwrite')
    regular(identity, private=True)
    if shutil.disk_usage(target.parent).free < 2 * cfg['max_restore_bytes'] + cfg['max_snapshot_bytes'] + cfg['min_free_bytes']:
        raise Failure('Insufficient space for bounded isolated restore')
    with tempfile.TemporaryDirectory(prefix='.athenaeum-restore-', dir=target.parent) as temp:
        temp = Path(temp)
        encrypted = temp / 'snapshot.age'
        if archive:
            regular(archive)
            if not expected_sha256 or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
                raise Failure('Offline restore requires the recorded ciphertext SHA256')
            if Path(archive).stat().st_size > cfg['max_snapshot_bytes'] or digest(archive) != expected_sha256:
                raise Failure('Offline ciphertext checksum/size does not match')
            shutil.copyfile(archive, encrypted)
        else:
            store = open_store(cfg) if store is None else store
            commit = select_commit(cfg, store, temp, identifier or '')
            if not commit:
                raise Failure('Snapshot is missing or not committed')
            store.get(commit['object'], encrypted, cfg['max_snapshot_bytes'], commit['_payload']['etag'])
            if digest(encrypted) != commit['sha256'] or encrypted.stat().st_size != commit['bytes']:
                raise Failure('Ciphertext checksum mismatch; refusing restore')
        decrypted = temp / 'snapshot.tar.gz'
        # Limit age's output file even before archive decompression/validation.
        def limit_output():
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE, (cfg['max_restore_bytes'], cfg['max_restore_bytes']))
        run([cfg['age_binary'], '--decrypt', '--identity', identity, '--output', decrypted, encrypted],
            timeout=300, preexec_fn=limit_output)
        payload = temp / 'payload'
        payload.mkdir(mode=0o700)
        manifest = unpack_verified(decrypted, payload, cfg['max_restore_bytes'])
        # Reserve the final directory exclusively before moving validated files.
        target.mkdir(mode=0o700)
        try:
            for child in payload.iterdir():
                shutil.move(str(child), target / child.name)
        except BaseException:
            shutil.rmtree(target)
            raise
    return manifest
