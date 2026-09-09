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
            secret = (state / 'session-secret').read_bytes()
            config = (state / 'compose.env').read_bytes()
            (state / 'apps/quacktuaries/data/app.db').write_bytes(b'fixture')
            initialize()
            self.assertEqual((state / 'session-secret').read_bytes(), secret)
            self.assertEqual((state / 'compose.env').read_bytes(), config)
            (state / 'session-secret').unlink()
            with self.assertRaisesRegex(SystemExit, 'Restore session-secret'):
                initialize()
            self.assertFalse((state / 'session-secret').exists())


if __name__ == '__main__':
    unittest.main()
