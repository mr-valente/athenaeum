"""Every registered application owns one SQLite database and no uploads."""
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import time

from .common import APPS, Failure, regular, digest, run, compose_files, data_dir, secret_path

# Schema 1 archived only Quacktuaries under top-level application/hook/database
# keys. Schema 2 records one entry per registered application. Older recovery
# points must stay restorable, so both are validated and normalized here.
SCHEMAS = (1, 2)
HOOK = 'sqlite-v1'
DATABASE_FILES = ('app.db', 'app.db-wal', 'app.db-shm', 'app.db-journal')


def applications(manifest):
    """Application entries of a validated manifest: {name: {'hook', 'database'}}."""
    if manifest['schema'] == 1:
        return {'quacktuaries': {'hook': manifest['hook'], 'database': manifest['database']}}
    return manifest['applications']


def inspect_database(path, tables):
    regular(path)
    with contextlib.closing(sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise Failure('SQLite integrity or foreign-key check failed')
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
        names = {row[1] for row in schema if row[0] == 'table'}
        if not tables.issubset(names):
            raise Failure('Database is missing the registered application schema')
        return {'user_version': db.execute('PRAGMA user_version').fetchone()[0],
                'schema_sha256': __import__('hashlib').sha256(json.dumps(schema).encode()).hexdigest(),
                'row_counts': {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in sorted(tables)}}


def snapshot_database(source, destination, tables, timeout=60):
    regular(source)
    deadline = time.monotonic() + timeout
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise Failure('SQLite online snapshot timed out; no recovery point uploaded')
    with contextlib.closing(sqlite3.connect(Path(source).absolute().as_uri() + '?mode=ro', uri=True, timeout=2)) as src:
        with contextlib.closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
    return inspect_database(destination, tables)


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
    if {p.name for p in (root / 'apps').iterdir()} != set(APPS):
        raise Failure('Unregistered application data directories; add backup hooks before proceeding')
    sources = {}
    for app in APPS:
        data = data_dir(cfg, app)
        unexpected = [p.name for p in data.iterdir() if p.name not in DATABASE_FILES]
        if unexpected:
            raise Failure(f'Undeclared files in {app} data; register a consistent backup hook before proceeding')
        for file in data.iterdir():
            regular(file)
        if (data / 'app.db').is_file():
            sources[app] = regular(data / 'app.db')
        elif any(data.iterdir()):
            raise Failure(f'Journal files without a database in {app} data; inspect it before proceeding')
        # An empty directory is a registered app awaiting its first start.
    if sum(source.stat().st_size for source in sources.values()) > cfg['max_restore_bytes']:
        raise Failure('Live databases exceed the configured restore limit')
    required_space = sum(source.stat().st_size for source in sources.values()) * 4 + cfg['min_free_bytes']
    if shutil.disk_usage(root).free < required_space:
        raise Failure('Insufficient staging space for databases, archive and verification download')
    payload = staging / 'payload'
    payload.mkdir(mode=0o700)
    entries = {}
    for app in APPS:
        key_before = digest(regular(secret_path(cfg, app)))
        metadata = None
        if app in sources:
            database = payload / 'apps' / app / 'app.db'
            database.parent.mkdir(parents=True, mode=0o700)
            metadata = snapshot_database(sources[app], database, APPS[app]['tables'])
        stable_copy(secret_path(cfg, app), payload / 'secrets' / (app + '-session-secret'))
        if digest(payload / 'secrets' / (app + '-session-secret')) != key_before:
            raise Failure(f'Signing key for {app} changed during the database snapshot')
        entries[app] = {'hook': HOOK, 'database': metadata}
    stable_copy(cfg['compose_env'], payload / 'config/compose.env')
    stable_copy(cfg['_source'], payload / 'config/recovery.json')
    for index, file in enumerate(compose_files(cfg)):
        stable_copy(file, payload / f'config/compose-{index}.yaml')
    # TLS certificates can be reissued on a replacement VM. Never copy Caddy's
    # actively changing certificate cache as if it were a consistent snapshot.
    manifest = {'schema': 2, 'created_at': datetime.now(timezone.utc).isoformat(),
                'applications': entries, 'images': images, 'tls_recovery': 'reissue', 'files': {}}
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
        if manifest['schema'] not in SCHEMAS:
            raise ValueError()
        if manifest['schema'] == 1 and (manifest['application'] != 'quacktuaries' or manifest['database'] is None):
            raise ValueError()
        entries = applications(manifest)
        # Only registered applications restore; a snapshot may predate some of them.
        if not entries or not set(entries).issubset(APPS):
            raise ValueError()
        if set(manifest['files']) != names - {'manifest.json'}:
            raise ValueError()
        required = {'config/compose.env', 'config/recovery.json', 'config/compose-0.yaml'}
        for app, entry in entries.items():
            if entry['hook'] != HOOK:
                raise ValueError()
            required.add(f'secrets/{app}-session-secret')
            if entry['database'] is not None:
                required.add(f'apps/{app}/app.db')
            elif f'apps/{app}/app.db' in names:
                raise ValueError()
        if not required.issubset(names):
            raise ValueError()
        for name, info in manifest['files'].items():
            file = target / name
            if file.stat().st_size != info['bytes'] or digest(file) != info['sha256']:
                raise ValueError()
        for app, entry in entries.items():
            if entry['database'] is not None and inspect_database(target / 'apps' / app / 'app.db', APPS[app]['tables']) != entry['database']:
                raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise Failure('Snapshot manifest, checksums, schema or database counts do not validate') from None
    return manifest
