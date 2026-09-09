import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops.common import Failure, atomic_json, digest, guard_mount, load_config, operation_lock, preflight, run
from athenaeum_ops.archive import inspect_database, snapshot_database, unpack_verified
from athenaeum_ops.recovery import backup, export_snapshot, restore, retention_keep, cleanup_staging
from athenaeum_ops.storage import LocalStore, OCIStore

AGE = os.environ.get('ATHENAEUM_TEST_AGE') or shutil.which('age') or ''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'live'
        for relative in ('apps/quacktuaries/data', 'edge/data', 'edge/config', 'backups/staging', 'deploy-state'):
            (self.data / relative).mkdir(parents=True, mode=0o700)
        self.database = self.data / 'apps/quacktuaries/data/app.db'
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            for table in ('teachers', 'sessions', 'players', 'device_stats', 'events'):
                db.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO teachers(value) VALUES ('synthetic teacher')")
        for name in ('state', 'objects'):
            (self.root / name).mkdir(mode=0o700)
        for name in ('secret', 'compose.env', 'compose.yaml'):
            (self.root / name).write_text('fixture-' * 8)
            (self.root / name).chmod(0o600)
        self.cfg = {'schema': 1, 'mode': 'local', 'filesystem_uuid': '', 'data_root': str(self.data),
                    'state_dir': str(self.root / 'state'), 'compose_project': 'fixture',
                    'compose_files': [str(self.root / 'compose.yaml')], 'compose_env': str(self.root / 'compose.env'),
                    'session_secret': str(self.root / 'secret'), 'recipient': 'age1' + 'a' * 58,
                    'age_binary': AGE, 'bucket_cap_bytes': 10_000_000, 'max_snapshot_bytes': 2_000_000,
                    'max_restore_bytes': 5_000_000, 'min_free_bytes': 1000, 'stale_after_seconds': 7200,
                    'store': {'kind': 'local', 'directory': str(self.root / 'objects'), 'prefix': 'athenaeum/v1/'}}
        self.config = self.root / 'recovery.json'
        atomic_json(self.config, self.cfg)
        self.cfg = load_config(self.config)
        self.store = LocalStore(self.cfg['store'])
        self.images = {'quacktuaries': {'id': 'sha256:' + 'a' * 64, 'digests': [], 'architecture': 'amd64'}}


class Guards(Fixture):
    def test_cleanup_only_removes_owned_marked_staging(self):
        staging = self.data / 'backups/staging'
        managed = staging / 'snapshot-abcdefgh'
        managed.mkdir(mode=0o700)
        (managed / '.athenaeum-staging').touch()
        (managed / 'plaintext').write_text('fixture')
        unrelated = staging / 'operator-notes'
        unrelated.mkdir()
        cleanup_staging(self.cfg)
        self.assertFalse(managed.exists())
        self.assertTrue(unrelated.exists())

    def test_real_missing_mount_and_mismatched_uuid_fail(self):
        cfg = dict(self.cfg, mode='production', filesystem_uuid='12345678-abcd')
        with self.assertRaises(Failure):
            guard_mount(cfg)  # real findmnt: temp directory is not a mountpoint
        for fs in ({'target': str(self.data), 'uuid': 'wrong', 'fstype': 'ext4', 'options': 'rw'},
                   {'target': str(self.data), 'uuid': cfg['filesystem_uuid'], 'fstype': 'ext4', 'options': 'ro'}):
            with patch('athenaeum_ops.common.run', return_value=json.dumps({'filesystems': [fs]}).encode()):
                with self.assertRaises(Failure):
                    guard_mount(cfg)
        fs = {'target': str(self.data), 'uuid': cfg['filesystem_uuid'], 'fstype': 'ext4', 'options': 'rw,relatime'}
        with patch('athenaeum_ops.common.run', return_value=json.dumps({'filesystems': [fs]}).encode()):
            self.assertTrue(guard_mount(cfg)['mount_verified'])

    def test_lock_excludes_parallel_operation(self):
        with operation_lock(self.cfg):
            with self.assertRaisesRegex(Failure, 'holds the host lock'):
                with operation_lock(self.cfg):
                    self.fail('second lock must fail')

    def test_missing_secret_symlink_and_low_disk_fail(self):
        secret = self.root / 'secret'
        secret.unlink()
        with self.assertRaises(Failure):
            preflight(self.cfg)
        secret.symlink_to(self.root / 'compose.env')
        with self.assertRaises(Failure):
            preflight(self.cfg)
        secret.unlink()
        secret.write_text('x' * 64)
        secret.chmod(0o600)
        with patch('athenaeum_ops.common.shutil.disk_usage', return_value=type('Usage', (), {'free': 1})()):
            with self.assertRaisesRegex(Failure, 'free space'):
                preflight(self.cfg)

    def test_online_backup_with_live_wal_writer_is_consistent(self):
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
        stopped = threading.Event()
        def write():
            with contextlib.closing(sqlite3.connect(self.database)) as db, db:
                while not stopped.is_set():
                    db.execute("INSERT INTO events(value) VALUES ('one')")
                    db.execute("INSERT INTO events(value) VALUES ('two')")
                    db.commit()
        thread = threading.Thread(target=write)
        thread.start()
        try:
            meta = snapshot_database(self.database, self.root / 'copy.db')
            self.assertEqual(meta['row_counts']['events'] % 2, 0)
        finally:
            stopped.set()
            thread.join()
        self.assertEqual(meta, inspect_database(self.root / 'copy.db'))

    def test_retention_keeps_latest_per_hour_day_and_newest(self):
        now = datetime.now(timezone.utc)
        commits = [{'id': str(i), 'created_at': (now - timedelta(minutes=30*i)).isoformat()} for i in range(500)]
        keep = retention_keep(commits)
        self.assertIn('0', keep)
        self.assertLessEqual(len(keep), 31)
        self.assertNotIn('1', keep) if now.minute >= 30 else None
        self.assertNotIn('499', keep)

    def test_archive_rejects_traversal_links_duplicates_and_bombs(self):
        for kind in ('traversal', 'link', 'duplicate', 'oversize'):
            archive = self.root / (kind + '.tgz')
            with tarfile.open(archive, 'w:gz') as tar:
                info = tarfile.TarInfo('../escape' if kind == 'traversal' else 'file')
                if kind == 'link':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/etc/passwd'
                    tar.addfile(info)
                else:
                    info.size = 20
                    tar.addfile(info, io.BytesIO(b'x' * 20))
                    if kind == 'duplicate':
                        tar.addfile(info, io.BytesIO(b'x' * 20))
            target = self.root / kind
            target.mkdir()
            with self.assertRaises(Failure):
                unpack_verified(archive, target, 10 if kind == 'oversize' else 1000)
        self.assertFalse((self.root / 'escape').exists())


@unittest.skipUnless(Path(AGE).is_file(), 'Set ATHENAEUM_TEST_AGE to run real age encryption checks')
class EncryptedRecovery(Fixture):
    def setUp(self):
        super().setUp()
        self.identity = self.root / 'identity'
        run([str(Path(AGE).with_name('age-keygen')), '-o', self.identity])
        self.identity.chmod(0o600)
        self.cfg['recipient'] = run([str(Path(AGE).with_name('age-keygen')), '-y', self.identity]).decode().strip()
        atomic_json(self.config, {k: v for k, v in self.cfg.items() if not k.startswith('_')})

    def test_roundtrip_offline_export_and_explicit_target(self):
        with operation_lock(self.cfg):
            result = backup(self.cfg, self.store, self.images)
        self.assertEqual(list((self.data / 'backups/staging').iterdir()), [])
        target = self.root / 'restored'
        manifest = restore(self.cfg, target, self.identity, result['snapshot'], store=self.store)
        self.assertEqual(manifest['database']['row_counts']['teachers'], 1)
        self.assertEqual((target / 'secrets/quacktuaries-session-secret').read_bytes(), (self.root / 'secret').read_bytes())
        with self.assertRaises(Failure):
            restore(self.cfg, target, self.identity, result['snapshot'], store=self.store)
        with self.assertRaises(Failure):
            restore(self.cfg, self.data / 'restore', self.identity, result['snapshot'], store=self.store)
        exported = self.root / 'external.age'
        info = export_snapshot(self.cfg, result['snapshot'], exported, self.store)
        offline = restore(self.cfg, self.root / 'offline', self.identity, archive=exported, expected_sha256=info['ciphertext_sha256'])
        self.assertEqual(offline['database'], manifest['database'])

    def test_full_bucket_preserves_previous_points_and_cleans_plaintext(self):
        previous = backup(self.cfg, self.store, self.images)
        objects, _ = self.store.inventory()
        before = {o['name']: o['etag'] for o in objects}
        cfg = dict(self.cfg, bucket_cap_bytes=sum(o['size'] for o in objects))
        with self.assertRaisesRegex(Failure, 'capacity'):
            backup(cfg, self.store, self.images)
        self.assertEqual(before, {o['name']: o['etag'] for o in self.store.inventory()[0]})
        self.assertFalse(list((self.data / 'backups/staging').iterdir()))

    def test_retention_only_prunes_registered_points_after_success(self):
        old = backup(self.cfg, self.store, self.images)
        unrelated = self.root / 'unrelated'
        unrelated.write_text('do not delete')
        self.store.put('other-project/keep', unrelated)
        newer = backup(self.cfg, self.store, self.images)
        # Two points in the same UTC hour: the older one is superseded.
        names = {item['name'] for item in self.store.inventory()[0]}
        self.assertIn(newer['object'], names)
        self.assertIn('other-project/keep', names)
        self.assertEqual(newer['pruned'], 1)
        self.assertNotIn(old['object'], names)

    def test_multipart_and_unrelated_objects_count_before_upload(self):
        objects, _ = self.store.inventory()
        with patch.object(self.store, 'inventory', return_value=(objects, self.cfg['bucket_cap_bytes'])):
            with self.assertRaisesRegex(Failure, 'capacity'):
                backup(self.cfg, self.store, self.images)
        self.assertEqual(self.store.inventory()[0], [])

    def test_manifest_checksum_failure_never_publishes_restored_directory(self):
        result = backup(self.cfg, self.store, self.images)
        good = self.root / 'good'
        restore(self.cfg, good, self.identity, result['snapshot'], store=self.store)
        (good / 'secrets/quacktuaries-session-secret').write_text('tampered')
        tarpath = self.root / 'tampered.tar.gz'
        with tarfile.open(tarpath, 'w:gz') as tar:
            for file in good.rglob('*'):
                if file.is_file():
                    tar.add(file, arcname=file.relative_to(good).as_posix())
        encrypted = self.root / 'tampered.age'
        run([AGE, '-r', self.cfg['recipient'], '-o', encrypted, tarpath])
        with self.assertRaisesRegex(Failure, 'checksums'):
            restore(self.cfg, self.root / 'invalid', self.identity, archive=encrypted, expected_sha256=digest(encrypted))
        self.assertFalse((self.root / 'invalid').exists())

    def test_corrupt_upload_never_commits_or_prunes(self):
        backup(self.cfg, self.store, self.images)
        before = {o['name']: o['etag'] for o in self.store.inventory()[0]}
        original_get = self.store.get
        def corrupt(name, dest, maximum, etag=None):
            original_get(name, dest, maximum, etag)
            if name.endswith('.age'):
                Path(dest).write_bytes(b'corrupt')
        with patch.object(self.store, 'get', side_effect=corrupt):
            with self.assertRaisesRegex(Failure, 'verification'):
                backup(self.cfg, self.store, self.images)
        after = {o['name']: o['etag'] for o in self.store.inventory()[0]}
        for name in before:
            self.assertEqual(before[name], after[name])
        self.assertEqual(sum('/commits/' in k for k in before), sum('/commits/' in k for k in after))
        self.assertFalse(list((self.data / 'backups/staging').iterdir()))

    def test_missing_corrupt_or_wrong_key_restore_leaves_no_target(self):
        result = backup(self.cfg, self.store, self.images)
        target = self.root / 'must-not-exist'
        wrong = self.root / 'wrong-key'
        run([str(Path(AGE).with_name('age-keygen')), '-o', wrong])
        wrong.chmod(0o600)
        with self.assertRaises(Failure):
            restore(self.cfg, target, wrong, result['snapshot'], store=self.store)
        self.assertFalse(target.exists())
        payload = self.store.path(result['object'])
        payload.write_bytes(b'corrupt')
        with self.assertRaises(Failure):
            restore(self.cfg, target, self.identity, result['snapshot'], store=self.store)
        payload.unlink()
        with self.assertRaises(Failure):
            restore(self.cfg, target, self.identity, result['snapshot'], store=self.store)
        self.assertFalse(target.exists())

    def test_unregistered_files_refused(self):
        (self.database.parent / 'upload.txt').write_text('not registered')
        with self.assertRaisesRegex(Failure, 'Undeclared'):
            backup(self.cfg, self.store, self.images)
        self.assertEqual(self.store.inventory()[0], [])


if __name__ == '__main__':
    unittest.main()
