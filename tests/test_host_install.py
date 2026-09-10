"""Read-only installer preview and additive firewall behavior."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / 'ops'


class HostSetupTests(unittest.TestCase):
    def test_metadata_rule_is_idempotent_and_does_not_flush_firewall(self):
        for present in (True, False):
            calls = []
            def execute(args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=0 if present or '-C' not in args or len(calls) > 2 else 1)
            with patch('subprocess.run', side_effect=execute), redirect_stdout(io.StringIO()):
                runpy.run_path(str(OPS / 'metadata-guard'))
            self.assertEqual(sum('-I' in call for call in calls), 0 if present else 1)
            self.assertFalse(any('-F' in call or '-D' in call for call in calls))
            self.assertTrue(all('DOCKER-USER' in call for call in calls))

    def test_preview_does_not_install_or_execute_host_commands(self):
        namespace = runpy.run_path(str(OPS / 'install-host'))
        install = namespace['install']
        globals_ = install.__globals__
        cfg = {'mode': 'production', 'data_root': '/srv/athenaeum', 'state_dir': '/var/lib/athenaeum',
               'age_binary': '/opt/athenaeum/tools/age', 'filesystem_uuid': '1234-abcd',
               'session_secret': '/etc/athenaeum/key', 'bernoulli_session_secret': '/etc/athenaeum/bernoulli-key',
               'compose_env': '/etc/athenaeum/compose.env',
               'compose_files': ['/opt/athenaeum/stack/compose.yaml']}
        with patch.dict(globals_, {'load_config': lambda p: cfg, 'guard_mount': lambda c: None,
                                  'regular': lambda *a, **k: None, 'run': lambda *a, **k: self.fail('preview ran host command')}):
            with patch('platform.machine', return_value='aarch64'), redirect_stdout(io.StringIO()) as output:
                install(SimpleNamespace(config='fixture', apply=False))
        self.assertFalse(json.loads(output.getvalue())['services_started'])

    def test_key_setup_refuses_orphaned_data_and_never_replaces_existing_key(self):
        ns = runpy.run_path(str(OPS / 'install-host'))
        prepare = ns['prepare_session_key']
        for app, key in (('quacktuaries', 'session_secret'), ('bernoulli', 'bernoulli_session_secret')):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                data = root / 'apps' / app / 'data'
                data.mkdir(parents=True)
                secret = root / (app + '-key')
                cfg = {'session_secret': str(root / 'unused'), 'bernoulli_session_secret': str(root / 'unused'),
                       'data_root': str(root), key: str(secret)}
                (data / 'app.db').write_text('existing records')
                with self.assertRaisesRegex(ns['Failure'], 'original key'): prepare(cfg, app)
                self.assertFalse(secret.exists())
                (data / 'app.db').unlink()
                # Ownership checks are mocked; file creation and preservation are real.
                original_stat = Path.stat
                def stat(path, *args, **kwargs):
                    result = original_stat(path, *args, **kwargs)
                    if path == secret:
                        fields = list(result)
                        fields[4] = 10001
                        return os.stat_result(fields)
                    return result
                with patch('os.chown') as chown, patch.object(Path, 'stat', stat):
                    prepare(cfg, app)
                    before = secret.read_bytes()
                    self.assertGreaterEqual(len(before.strip()), 32)
                    prepare(cfg, app)
                    self.assertEqual(secret.read_bytes(), before)
                    self.assertEqual(chown.call_count, 1)

    def test_installer_saves_existing_file_before_replacing_and_is_idempotent(self):
        namespace = runpy.run_path(str(OPS / 'install-host'))
        install_file = namespace['install_file']
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, dest, rollback = root / 'source', root / 'dest', root / 'rollback'
            source.write_text('new config')
            dest.write_text('old config')
            install_file(source, dest, rollback, 0o600)
            saved = rollback / dest.relative_to('/')
            self.assertEqual(saved.read_text(), 'old config')
            self.assertEqual(dest.read_text(), 'new config')
            install_file(source, dest, rollback, 0o600)
            self.assertEqual(saved.read_text(), 'old config')
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_install_failure_keeps_private_diagnostics_and_reports_stage(self):
        step = runpy.run_path(str(OPS / 'install-host'))['install_step']
        import sys
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'step.log'
            with redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(Exception, 'Preparing Python: exited with status 7') as caught:
                    step([sys.executable, '-c', "import sys; print('private-index-token', file=sys.stderr); sys.exit(7)"],
                         'Preparing Python', log)
            self.assertIn('sudo less ' + str(log), str(caught.exception))
            self.assertNotIn('private-index-token', str(caught.exception) + output.getvalue())
            self.assertIn('private-index-token', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_install_timeout_reports_deadline_and_preserves_output(self):
        step = runpy.run_path(str(OPS / 'install-host'))['install_step']
        def execute(args, **kwargs):
            kwargs['stdout'].write('partial diagnostic\n')
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        with tempfile.TemporaryDirectory() as temp, patch('subprocess.run', side_effect=execute), redirect_stdout(io.StringIO()):
            log = Path(temp) / 'timeout.log'
            with self.assertRaisesRegex(Exception, 'timed out after 180 seconds'):
                step(['python3', '-m', 'venv', '/fixture'], 'Preparing Python', log)
            self.assertEqual(log.read_text(), 'partial diagnostic\n')

    def test_incomplete_venv_is_repaired_even_when_python_exists(self):
        prepare = runpy.run_path(str(OPS / 'install-host'))['prepare_venv']
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            venv = Path(temp) / 'venv'
            (venv / 'bin').mkdir(parents=True)
            (venv / 'bin/python').touch()
            with patch.dict(prepare.__globals__, {'install_step': lambda args, *a, **kw: calls.append(args)}):
                prepare(venv, Path(temp))
            self.assertEqual(calls, [['python3', '-m', 'venv', venv], [venv / 'bin/python', '-m', 'pip', '--version']])
            self.assertTrue((venv / 'bin/python').exists())
