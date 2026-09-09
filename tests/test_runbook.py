"""Runbook safety and sequencing without root, cloud access or real host changes."""
from contextlib import nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

RUNBOOK = Path(__file__).resolve().parents[1] / 'ops/runbook'
sys.path.insert(0, str(RUNBOOK))
import _support
from athenaeum_ops.common import Failure


def script(name):
    return runpy.run_path(str(RUNBOOK / name))


class RunbookTests(unittest.TestCase):
    def test_wrong_host_stops_before_setup(self):
        with patch('_support.platform.freedesktop_os_release', return_value={'ID': 'arch'}):
            with self.assertRaisesRegex(Failure, 'Ubuntu 26.04 ARM'):
                _support.host()

    def test_fstab_preserves_other_rules_and_is_idempotent(self):
        plan = script('mount-data')['planned_fstab']
        before = '# OS disk\nUUID=boot / ext4 defaults 0 1\n'
        after = plan(before, '1234-abcd')
        self.assertTrue(after.startswith(before))
        self.assertEqual(plan(after, '1234-abcd'), after)
        for conflict in ('UUID=wrong /srv/athenaeum ext4 defaults 0 2\n',
                         'UUID=1234-abcd / ext4 defaults 0 1\n',
                         after + after):
            with self.subTest(conflict=conflict), self.assertRaises(Failure):
                plan(conflict, '1234-abcd')

    def test_mount_preview_never_writes_or_mounts(self):
        main = script('mount-data')['main']
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            fstab = base / 'fstab'
            fstab.write_text('# preserved\n')
            target = base / 'data'
            def run(args):
                if args[0] == 'findmnt':
                    return b'{"filesystems":[{"target":"/","uuid":"boot"}]}'
                return b'ext4\n' if 'TYPE' in args else b'/dev/fixture\n'
            with patch.dict(main.__globals__, {'host': lambda: None, 'run': run, 'FSTAB': fstab, 'TARGET': target,
                                              'visible': lambda a: self.fail('preview mutated host')}), \
                 patch.object(sys, 'argv', ['mount-data', '--uuid', '1234-abcd']), redirect_stdout(io.StringIO()):
                main()
            self.assertEqual(fstab.read_text(), '# preserved\n')
            self.assertFalse(target.exists())

    def test_mount_apply_saves_verified_backup_and_preserves_fstab_mode(self):
        main = script('mount-data')['main']
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            fstab = base / 'fstab'
            fstab.write_text('# preserve OS entry\nUUID=boot / ext4 defaults 0 1\n')
            before = fstab.read_bytes()
            fstab.chmod(0o640)
            calls = []
            target = base / 'data'
            def run(args):
                if args[0] == 'findmnt':
                    return b'{"filesystems":[{"target":"/","uuid":"boot"}]}'
                return b'ext4\n' if 'TYPE' in args else b'/dev/fixture\n'
            with patch.dict(main.__globals__, {'host': lambda: None, 'run': run, 'FSTAB': fstab, 'TARGET': target,
                    'visible': lambda args: calls.append(args), 'guard_mount': lambda cfg: calls.append(['guard'])}), \
                 patch('os.fchown'), \
                 patch.object(sys, 'argv', ['mount-data', '--uuid', '1234-abcd', '--apply']), redirect_stdout(io.StringIO()):
                main()
            backups = list(base.glob('fstab.before-athenaeum-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before)
            self.assertTrue(fstab.read_bytes().startswith(before))
            self.assertEqual(fstab.stat().st_mode & 0o777, 0o640)
            self.assertIn(['mount', str(target)], calls)
            self.assertEqual(list(base.glob('.fstab-athenaeum-*')), [])

    def test_bad_redirect_does_not_pass_https_check(self):
        probe = script('verify-site')['probe']
        class Response:
            code = 308
            headers = {'Location': '/wrong'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        opener = SimpleNamespace(open=lambda *a, **k: Response())
        with self.assertRaisesRegex(Failure, 'HTTPS check failed'):
            probe(opener, 'https://example.test/quacktuaries?source=setup', 308,
                  'https://example.test/quacktuaries/?source=setup')
        Response.headers = {'Location': '/quacktuaries/?source=setup'}
        with redirect_stdout(io.StringIO()):
            probe(opener, 'https://example.test/quacktuaries?source=setup', 308,
                  'https://example.test/quacktuaries/?source=setup')

    def test_settings_preserve_existing_and_reject_partial_or_symlink(self):
        existing = script('configure-host')['existing']
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.assertFalse(existing(directory))
            (directory / 'compose.env').write_text('do not replace')
            (directory / 'compose.env').chmod(0o600)
            with self.assertRaisesRegex(Failure, 'Only one'):
                existing(directory)
            (directory / 'recovery.json').write_text('{}')
            (directory / 'recovery.json').chmod(0o600)
            self.assertTrue(existing(directory))
            self.assertEqual((directory / 'compose.env').read_text(), 'do not replace')
            (directory / 'recovery.json').unlink()
            (directory / 'recovery.json').symlink_to(directory / 'compose.env')
            with self.assertRaisesRegex(Failure, 'Symlink'):
                existing(directory)

    def test_update_holds_one_lock_and_stops_on_backup_or_pull_failure(self):
        operate = script('stack')['operate']
        for failure in (None, 'backup', 'pull', 'up'):
            events = []
            class Lock:
                def __enter__(self): events.append('locked')
                def __exit__(self, *exc): events.append('unlocked')
            def backup(cfg):
                events.append('backup')
                if failure == 'backup': raise Failure('backup failed')
                return {'snapshot': 'fixture'}
            def compose(cfg, *args):
                events.append(args[0])
                if args[0] == failure: raise Failure('Docker failed')
                return b'healthy'
            with patch.dict(operate.__globals__, {'operation_lock': lambda c: Lock(), 'preflight': lambda c: None,
                    'validate_compose': lambda c: None, 'run': lambda a: b'', 'recorded_backup': backup, 'compose': compose}), \
                 redirect_stdout(io.StringIO()):
                if failure:
                    with self.assertRaises(Failure): operate({}, 'update')
                else:
                    operate({}, 'update')
            expected = ['locked', 'backup']
            if failure != 'backup': expected += ['pull']
            if failure not in ('backup', 'pull'): expected += ['up']
            if failure is None: expected += ['ps']
            self.assertEqual(events, expected + ['unlocked'])

    def test_start_refuses_existing_records_before_pull(self):
        operate = script('stack')['operate']
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp, 'apps/quacktuaries/data')
            data.mkdir(parents=True)
            (data / 'app.db').write_text('existing records')
            with patch.dict(operate.__globals__, {'operation_lock': lambda c: nullcontext(), 'preflight': lambda c: None,
                    'validate_compose': lambda c: None, 'run': lambda a: b'',
                    'compose': lambda *a: self.fail('Existing records reached Compose pull/up')}):
                with self.assertRaisesRegex(Failure, 'Existing app data'):
                    operate({'data_root': temp}, 'start')

    def test_recovery_uses_exact_image_and_cleans_only_its_private_key_on_failure(self):
        main = script('check-recovery')['main']
        for fail_restore in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                identity = base / 'original-key'
                identity.write_text('synthetic private identity')
                identity.chmod(0o600)
                output = base / 'backup.age'
                work = base / 'inspection'
                events, key_paths = [], []
                # Use real temp directories so cleanup is tested, but redirect
                # /run and /var/tmp into this unprivileged fixture directory.
                tempdir = tempfile.TemporaryDirectory
                mkdtemp = tempfile.mkdtemp
                def restore(cfg, target, key, identifier=None):
                    events.append(('restore', identifier))
                    key_paths.append(Path(key))
                    self.assertEqual(Path(key).read_text(), identity.read_text())
                    if fail_restore: raise Failure('bad recovery')
                    return {'fixture': 'manifest'}
                def runtime(target, image, manifest):
                    events.append(('runtime', image))
                def export(cfg, snapshot, path):
                    events.append(('export', snapshot))
                    Path(path).write_bytes(b'ciphertext')
                    return {'ciphertext_sha256': 'a' * 64}
                with patch.dict(main.__globals__, {
                        'installed_config': lambda: {}, 'recovery_python': lambda: None,
                        'operation_lock': lambda c: nullcontext(),
                        'deployment': lambda c: {'quacktuaries': {'id': 'sha256:exact-current-image'}},
                        'recorded_backup': lambda c: {'snapshot': 'explicit-snapshot'},
                        'restore': restore, 'test_runtime': runtime, 'export_snapshot': export,
                        'write_status': lambda c, k, v: events.append(('status', v['status'])),
                        'tempfile': SimpleNamespace(TemporaryDirectory=lambda **kw: tempdir(prefix='key-', dir=base),
                                                    mkdtemp=lambda **kw: mkdtemp(prefix='inspection-', dir=base))}), \
                     patch('os.chown'), \
                     patch.object(sys, 'argv', ['check-recovery', '--identity', str(identity), '--export', str(output)]), \
                     redirect_stdout(io.StringIO()):
                    if fail_restore:
                        with self.assertRaises(Failure): main()
                    else:
                        main()
                self.assertTrue(identity.exists())
                self.assertTrue(key_paths)
                self.assertTrue(all(not key.exists() for key in key_paths))
                self.assertEqual(events, [('status', 'running'), ('restore', 'explicit-snapshot')] if fail_restore else [
                    ('status', 'running'), ('restore', 'explicit-snapshot'), ('runtime', 'sha256:exact-current-image'),
                    ('export', 'explicit-snapshot'), ('status', 'ok')])

    def test_download_mismatch_does_not_publish_or_overwrite(self):
        main = script('download-backup')['main']
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, 'backup.age')
            def scp(args):
                Path(args[-1]).write_bytes(b'wrong bytes')
            with patch.dict(main.__globals__, {'visible': scp}), patch('os.geteuid', return_value=1000), \
                 patch.object(sys, 'argv', ['download-backup', '--host', '192.0.2.1', '--remote', '/home/ubuntu/test.age',
                                           '--sha256', 'a' * 64, '--output', str(output)]):
                with self.assertRaisesRegex(Failure, 'Checksum mismatch'): main()
                self.assertFalse(output.exists())
                output.write_bytes(b'previous backup')
                with self.assertRaisesRegex(Failure, 'already exists'): main()
                self.assertEqual(output.read_bytes(), b'previous backup')


if __name__ == '__main__':
    unittest.main()
