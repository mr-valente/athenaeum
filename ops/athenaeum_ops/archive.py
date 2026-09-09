"""The current registered application owns one SQLite database and no uploads."""
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import time

from .common import Failure, regular, digest, run, compose_files

TABLES = {'teachers', 'sessions', 'players', 'device_stats', 'events'}


def inspect_database(path):
    regular(path)
    with contextlib.closing(sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise Failure('SQLite integrity or foreign-key check failed')
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
        names = {row[1] for row in schema if row[0] == 'table'}
        if not TABLES.issubset(names):
            raise Failure('Database is missing the registered Quacktuaries schema')
        return {'user_version': db.execute('PRAGMA user_version').fetchone()[0],
                'schema_sha256': __import__('hashlib').sha256(json.dumps(schema).encode()).hexdigest(),
                'row_counts': {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in sorted(TABLES)}}


def snapshot_database(source, destination, timeout=60):
    regular(source)
    deadline = time.monotonic() + timeout
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise Failure('SQLite online snapshot timed out; no recovery point uploaded')
    with contextlib.closing(sqlite3.connect(Path(source).absolute().as_uri() + '?mode=ro', uri=True, timeout=2)) as src:
        with contextlib.closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
    return inspect_database(destination)


def stable_copy(source, target):
    source = regular(source)
    before = source.stat()
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise Failure('Recovery configuration changed during snapshot; retry under the host lock')


def create_archive(cfg, staging, images):
    root = Path(cfg['data_root'])
    if {p.name for p in (root / 'apps').iterdir()} != {'quacktuaries'}:
        raise Failure('Unregistered application data directories; add backup hooks before proceeding')
    data = root / 'apps/quacktuaries/data'
    unexpected = [p.name for p in data.iterdir() if p.name not in ('app.db', 'app.db-wal', 'app.db-shm', 'app.db-journal')]
    if unexpected:
        raise Failure('Undeclared files in Quacktuaries data; register a consistent backup hook before proceeding')
    for file in data.iterdir():
        regular(file)
    source = regular(data / 'app.db')
    if source.stat().st_size > cfg['max_restore_bytes']:
        raise Failure('Live database exceeds the configured restore limit')
    required_space = source.stat().st_size * 4 + cfg['min_free_bytes']
    if shutil.disk_usage(root).free < required_space:
        raise Failure('Insufficient staging space for database, archive and verification download')
    payload = staging / 'payload'
    payload.mkdir(mode=0o700)
    database = payload / 'apps/quacktuaries/app.db'
    database.parent.mkdir(parents=True, mode=0o700)
    key_before = digest(regular(cfg['session_secret']))
    metadata = snapshot_database(source, database)
    stable_copy(cfg['session_secret'], payload / 'secrets/quacktuaries-session-secret')
    if digest(payload / 'secrets/quacktuaries-session-secret') != key_before:
        raise Failure('Signing key changed during the database snapshot')
    stable_copy(cfg['compose_env'], payload / 'config/compose.env')
    stable_copy(cfg['_source'], payload / 'config/recovery.json')
    for index, file in enumerate(compose_files(cfg)):
        stable_copy(file, payload / f'config/compose-{index}.yaml')
    for name in ('releases.json',):
        state_file = root / 'deploy-state' / name
        if state_file.exists():
            stable_copy(state_file, payload / 'config' / name)
    # TLS certificates can be reissued on a replacement VM. Never copy Caddy's
    # actively changing certificate cache as if it were a consistent snapshot.
    manifest = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'application': 'quacktuaries', 'hook': 'sqlite-v1', 'database': metadata,
                'images': images, 'tls_recovery': 'reissue', 'files': {}}
    for file in sorted(payload.rglob('*')):
        if file.is_file():
            manifest['files'][file.relative_to(payload).as_posix()] = {'bytes': file.stat().st_size, 'sha256': digest(file)}
    (payload / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    if sum(p.stat().st_size for p in payload.rglob('*') if p.is_file()) > cfg['max_restore_bytes']:
        raise Failure('Snapshot payload exceeds configured restore limit')
    archive = staging / 'snapshot.tar.gz'
    with tarfile.open(archive, 'w:gz', format=tarfile.PAX_FORMAT) as tar:
        for file in sorted(payload.rglob('*')):
            if file.is_file():
                tar.add(file, arcname=file.relative_to(payload).as_posix(), recursive=False)
    encrypted = staging / 'snapshot.tar.gz.age'
    run([cfg['age_binary'], '--encrypt', '--recipient', cfg['recipient'], '--output', encrypted, archive], timeout=300)
    if encrypted.stat().st_size > cfg['max_snapshot_bytes']:
        raise Failure('Encrypted snapshot exceeds the configured single-snapshot limit')
    return encrypted


def unpack_verified(archive, target, maximum):
    """Never extract links, special files, duplicates, absolute paths or traversal."""
    total, names = 0, set()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or '\\' in member.name or path.as_posix() != member.name or member.name in names):
                raise Failure('Unsafe or duplicate archive member')
            names.add(member.name)
            total += member.size
            if total > maximum or len(names) > 1000:
                raise Failure('Restored archive exceeds its size/member limit')
            out = target / path
            out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tar.extractfile(member) as inp, open(out, 'xb') as file:
                shutil.copyfileobj(inp, file)
            os.chmod(out, 0o600)
    regular(target / 'manifest.json')
    if (target / 'manifest.json').stat().st_size > 1_000_000:
        raise Failure('Oversized snapshot manifest')
    try:
        manifest = json.loads((target / 'manifest.json').read_text())
        if manifest['schema'] != 1 or manifest['application'] != 'quacktuaries' or manifest['hook'] != 'sqlite-v1':
            raise ValueError()
        if set(manifest['files']) != names - {'manifest.json'}:
            raise ValueError()
        required = {'apps/quacktuaries/app.db', 'secrets/quacktuaries-session-secret',
                    'config/compose.env', 'config/recovery.json', 'config/compose-0.yaml'}
        if not required.issubset(names):
            raise ValueError()
        for name, info in manifest['files'].items():
            file = target / name
            if file.stat().st_size != info['bytes'] or digest(file) != info['sha256']:
                raise ValueError()
        if inspect_database(target / 'apps/quacktuaries/app.db') != manifest['database']:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise Failure('Snapshot manifest, checksums, schema or database counts do not validate') from None
    return manifest
