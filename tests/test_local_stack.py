"""The local initializer must never replace a key underneath existing records."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import runpy
import tempfile
import unittest


class LocalSetupTest(unittest.TestCase):
    def test_repeated_setup_preserves_state_and_missing_key_requires_recovery(self):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'ops/local-stack'))
        initialize = namespace['initialize']
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            state = Path(directory)
            initialize.__globals__['STATE'] = state
            initialize.__globals__['ENV'] = state / 'compose.env'
            initialize()
            secrets = {name: (state / name).read_bytes() for name in ('session-secret', 'bernoulli-session-secret')}
            config = (state / 'compose.env').read_bytes()
            self.assertIn(b'BERNOULLI_SECRET_FILE=', config)
            (state / 'apps/quacktuaries/data/app.db').write_bytes(b'fixture')
            (state / 'apps/bernoulli/data/app.db').write_bytes(b'fixture')
            initialize()
            for name, before in secrets.items():
                self.assertEqual((state / name).read_bytes(), before)
            self.assertEqual((state / 'compose.env').read_bytes(), config)
            for name in secrets:
                (state / name).unlink()
                with self.assertRaisesRegex(SystemExit, 'Restore ' + name):
                    initialize()
                self.assertFalse((state / name).exists())
                (state / name).write_bytes(secrets[name])

    def test_settings_written_before_a_new_app_gain_only_its_keys(self):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'ops/local-stack'))
        initialize = namespace['initialize']
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            state = Path(directory)
            initialize.__globals__['STATE'] = state
            initialize.__globals__['ENV'] = state / 'compose.env'
            older = 'DATA_ROOT="/elsewhere"\nLOCAL_HTTPS_PORT="4385"\nQUACKTUARIES_SECRET_FILE="/kept"\n'
            (state / 'compose.env').write_text(older)
            initialize()
            text = (state / 'compose.env').read_text()
            self.assertTrue(text.startswith(older))
            self.assertIn('BERNOULLI_SECRET_FILE=', text)
            self.assertIn('BERNOULLI_IMAGE="bernoulli:athenaeum-local"', text)
            self.assertEqual(text.count('LOCAL_HTTPS_PORT='), 1)
            self.assertEqual(text.count('QUACKTUARIES_SECRET_FILE='), 1)


if __name__ == '__main__':
    unittest.main()
