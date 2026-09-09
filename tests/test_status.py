"""Manual deployments still report container and backup failures to the operator."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops import cli
from athenaeum_ops.common import Failure, compose


class StatusTests(unittest.TestCase):
    def test_status_checks_containers_and_backup_without_release_state(self):
        healthy = [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in cli.SERVICES]
        for encoding in ('array', 'lines'):
            for case in ('healthy', 'missing', 'unhealthy', 'stale', 'docker-failed'):
                with self.subTest(encoding=encoding, case=case), tempfile.TemporaryDirectory() as temp:
                    containers = [dict(c) for c in healthy]
                    if case == 'missing':
                        containers.pop()
                    if case == 'unhealthy':
                        containers[0]['Health'] = 'unhealthy'
                    payload = (json.dumps(containers) if encoding == 'array' else
                               '\n'.join(json.dumps(c) for c in containers)).encode()
                    Path(temp, 'status.json').write_text(json.dumps({
                        'last_backup': {'verified_at': 0 if case == 'stale' else time.time()},
                        'last_attempt': {'status': 'ok'},
                    }))
                    cfg = {'state_dir': temp, 'stale_after_seconds': 7200}
                    with patch.object(sys, 'argv', ['athenaeumctl', 'status', '--json']), \
                         patch.object(cli, 'load_config', return_value=cfg), \
                         patch.object(cli, 'operation_lock', return_value=nullcontext()), \
                         patch.object(cli, 'preflight', return_value={'free_bytes': 10**10}), \
                         patch.object(cli, 'compose', return_value=payload,
                                      side_effect=Failure('Docker unavailable') if case == 'docker-failed' else None), \
                         redirect_stdout(io.StringIO()) as output:
                        result = cli.main()
                    self.assertEqual(result, 0 if case == 'healthy' else 1)
                    self.assertIn('stack', json.loads(output.getvalue()))

    def test_old_image_overlay_cannot_override_manual_compose_images(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp, 'deploy-state')
            state.mkdir()
            (state / 'images.json').write_text('{"services":{"athenaeum":{"image":"obsolete:old"}}}')
            cfg = {'data_root': temp, 'compose_project': 'fixture', 'compose_env': '/fixture/compose.env',
                   'compose_files': ['/fixture/compose.yaml']}
            with patch('athenaeum_ops.common.run', return_value=b'') as run:
                compose(cfg, 'config', '--quiet')
            command = run.call_args.args[0]
            self.assertNotIn(str(state / 'images.json'), command)
            self.assertIn('/fixture/compose.yaml', command)


if __name__ == '__main__':
    unittest.main()
