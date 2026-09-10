"""Command-center regressions: real Git fixtures, deployment gates, sudo forwarding."""
from contextlib import nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops import cli, commands, management, repository
from athenaeum_ops.common import Failure


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.origin = self.base / 'origin'
        self.origin.mkdir()
        self.git(self.origin, 'init', '-b', 'main')
        (self.origin / 'compose.yaml').write_text('initial\n')
        self.commit('initial')
        self.root = self.base / 'stack'
        self.git(self.base, 'clone', str(self.origin), str(self.root))
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        self.cfg = {'state_dir': str(self.state), 'compose_files': [str(self.root / 'compose.yaml')],
                    'compose_env': '/etc/athenaeum/compose.env'}
        self.addCleanup(patch.stopall)
        patch.object(repository, 'REMOTE', str(self.origin)).start()
        self.validate = patch.object(repository, 'validate_compose').start()
        self.output = patch('sys.stdout', new=io.StringIO()).start()

    def git(self, root, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                        '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, message, root=None):
        root = root or self.origin
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-m', message)

    def advance(self):
        (self.origin / 'compose.yaml').write_text('updated\n')
        self.commit('updated compose')

    def test_sync_validates_candidate_then_fast_forwards_without_owner_change(self):
        self.advance()
        owner = (self.root / '.git').stat().st_uid
        def validate(cfg):
            self.assertEqual((self.root / 'compose.yaml').read_text(), 'initial\n')
            self.assertEqual(Path(cfg['compose_files'][0]).read_text(), 'updated\n')
            self.assertEqual(cfg['compose_env'], self.cfg['compose_env'])
        self.validate.side_effect = validate
        repository.sync(self.cfg, self.root)
        self.assertEqual((self.root / 'compose.yaml').read_text(), 'updated\n')
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), self.git(self.origin, 'rev-parse', 'HEAD'))
        self.assertEqual((self.root / '.git/index').stat().st_uid, owner)
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), '')
        self.assertEqual(list(self.base.glob('.athenaeum-repo-*')), [])
        repository.sync(self.cfg, self.root)
        self.assertEqual(self.validate.call_count, 1)

    def test_dirty_or_untracked_files_stop_before_fetch(self):
        self.advance()
        remote_before = self.git(self.root, 'rev-parse', 'origin/main')
        for name in ('compose.yaml', 'local-notes'):
            target = self.root / name
            target.write_text('preserve me\n')
            with self.assertRaisesRegex(Failure, 'local edits'):
                repository.sync(self.cfg, self.root)
            self.assertEqual(target.read_text(), 'preserve me\n')
            self.assertEqual(self.git(self.root, 'rev-parse', 'origin/main'), remote_before)
            if name == 'compose.yaml':
                self.git(self.root, 'restore', name)
            else:
                target.unlink()

    def test_diverged_and_ahead_history_are_preserved(self):
        (self.root / 'local').write_text('local commit\n')
        self.commit('local work', self.root)
        before = self.git(self.root, 'rev-parse', 'HEAD')
        for divergent in (False, True):
            if divergent:
                self.advance()
            with self.assertRaisesRegex(Failure, 'ahead of or diverged'):
                repository.sync(self.cfg, self.root)
            self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), before)

    def test_invalid_candidate_leaves_live_files_and_head_unchanged(self):
        self.advance()
        before = self.git(self.root, 'rev-parse', 'HEAD')
        self.validate.side_effect = Failure('invalid candidate mounts')
        with self.assertRaisesRegex(Failure, 'invalid candidate'):
            repository.sync(self.cfg, self.root)
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), before)
        self.assertEqual((self.root / 'compose.yaml').read_text(), 'initial\n')
        self.assertEqual(list(self.base.glob('.athenaeum-repo-*')), [])

    def test_wrong_remote_or_branch_never_changes_worktree(self):
        self.git(self.root, 'checkout', '-b', 'local-work')
        with self.assertRaisesRegex(Failure, 'main branch'):
            repository.sync(self.cfg, self.root)
        self.git(self.root, 'checkout', 'main')
        self.git(self.root, 'remote', 'set-url', 'origin', '/unexpected')
        with self.assertRaisesRegex(Failure, 'Expected origin'):
            repository.sync(self.cfg, self.root)

    def test_adopt_preserves_all_copied_files_and_validates_before_swap(self):
        import shutil
        shutil.rmtree(self.root / '.git')
        (self.root / 'local-notes').write_text('preserve notes\n')
        self.advance()
        self.validate.side_effect = Failure('invalid candidate')
        with self.assertRaises(Failure):
            repository.adopt(self.cfg, self.root)
        self.assertEqual((self.root / 'local-notes').read_text(), 'preserve notes\n')
        self.assertFalse((self.root / '.git').exists())
        self.validate.side_effect = None
        repository.adopt(self.cfg, self.root)
        self.assertEqual((self.root / 'compose.yaml').read_text(), 'updated\n')
        backups = list(self.base.glob('.athenaeum-repo-*/previous-stack'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'local-notes').read_text(), 'preserve notes\n')
        self.assertTrue((self.root / '.git').is_dir())
        with self.assertRaisesRegex(Failure, 'already exists'):
            repository.adopt(self.cfg, self.root)

    def test_adopt_restores_original_directory_when_final_rename_fails(self):
        import shutil
        shutil.rmtree(self.root / '.git')
        original = Path.rename
        def rename(path, target):
            if path.name == 'candidate':
                raise OSError('simulated rename failure')
            return original(path, target)
        with patch.object(Path, 'rename', rename), self.assertRaises(OSError):
            repository.adopt(self.cfg, self.root)
        self.assertEqual((self.root / 'compose.yaml').read_text(), 'initial\n')
        self.assertFalse((self.root / '.git').exists())


class CommandTests(unittest.TestCase):
    def test_self_update_uses_installer_without_service_activation(self):
        cfg = {'mode': 'production', '_source': '/etc/athenaeum/recovery.json'}
        with patch.object(commands.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
             redirect_stdout(io.StringIO()):
            self.assertTrue(commands.dispatch(cfg, SimpleNamespace(command='self')))
        self.assertEqual(run.call_args.args[0], [sys.executable, '/opt/athenaeum/stack/ops/install-host',
                                               '--config', cfg['_source'], '--apply'])

    def test_backup_failure_prevents_pull_and_deploy(self):
        for action in ('update', 'deploy'):
            with patch.object(management, 'operation_lock', return_value=nullcontext()), \
                 patch.object(management, 'preflight'), patch.object(management, 'validate_compose'), \
                 patch.object(management, 'run'), patch.object(management, 'compose_visible') as docker, \
                 patch.object(management, 'recorded_backup', side_effect=Failure('backup unavailable')), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(Failure, 'backup unavailable'):
                    management.operate({}, action, visible=True)
                docker.assert_not_called()

    def test_deploy_applies_cached_images_and_pull_only_never_replaces(self):
        for action in ('deploy', 'pull', 'update'):
            events = []
            with patch.object(management, 'operation_lock', return_value=nullcontext()), \
                 patch.object(management, 'preflight'), patch.object(management, 'validate_compose'), \
                 patch.object(management, 'run'), patch.object(management, 'compose', return_value=b'healthy'), \
                 patch.object(management, 'compose_visible', side_effect=lambda cfg, *args: events.append(args)), \
                 patch.object(management, 'recorded_backup', side_effect=lambda cfg: events.append(('backup',)) or {'snapshot': 'test'}), \
                 redirect_stdout(io.StringIO()):
                management.operate({}, action, visible=True)
            self.assertEqual([e[0] for e in events], {'deploy': ['backup', 'up'], 'pull': ['pull'],
                                                     'update': ['backup', 'pull', 'up']}[action])
            for event in events:
                if event[0] == 'up':
                    self.assertIn('--no-build', event)
                    self.assertEqual(event[event.index('--pull') + 1], 'never')

    def test_cli_parses_examples_without_old_recovery_dispatch(self):
        for args in (['docker', 'pull', '--deploy'], ['repo', 'sync'], ['verify'],
                     ['docker', 'logs', 'quacktuaries', '-f'], ['self', 'update']):
            with patch.object(sys, 'argv', ['athenaeumctl', *args]), \
                 patch.object(cli, 'load_config', return_value={}), \
                 patch.object(commands, 'dispatch', return_value=True) as dispatch:
                self.assertEqual(cli.main(), 0)
            self.assertEqual(dispatch.call_args.args[1].command, args[0])

    def test_launcher_requests_existing_sudo_and_preserves_arguments(self):
        source = (Path(__file__).resolve().parents[1] / 'ops/athenaeum-launcher').read_text()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uid, sudo = root / 'id', root / 'sudo'
            uid.write_text('#!/bin/sh\necho 1000\n')
            sudo.write_text('#!/usr/bin/env python3\nimport json,sys; print(json.dumps(sys.argv[1:]))\n')
            uid.chmod(0o755); sudo.chmod(0o755)
            wrapper = root / 'ctl'
            wrapper.write_text(source.replace('/usr/bin/id', str(uid)).replace('/usr/bin/sudo', str(sudo)))
            result = subprocess.check_output(['sh', str(wrapper), 'export', '--output', '/tmp/with space.age'], cwd='/')
            self.assertEqual(json.loads(result), ['--', '/usr/local/sbin/athenaeumctl', 'export', '--output', '/tmp/with space.age'])


if __name__ == '__main__':
    unittest.main()
