"""Only fully checked native indexes may advance the deployment tag."""
import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def exercise(self, fail=False):
        namespace = runpy.run_path(str(ROOT / 'ci/images.py'))
        calls = []
        def execute(*args, **kwargs):
            calls.append(tuple(map(str, args)))
            if args[:4] == ('docker', 'buildx', 'imagetools', 'inspect'):
                if fail: raise RuntimeError('Registry missing manifest')
                return json.dumps({'digest': 'sha256:' + 'a' * 64, 'manifests': [
                    {'platform': {'os': 'linux', 'architecture': a}} for a in ('amd64', 'arm64')]}).encode()
            return b''
        environment = {'GITHUB_SHA': 'a' * 40, 'GITHUB_REPOSITORY': 'fixture/' + ROOT.name,
                       'DOCKERHUB_USERNAME': 'fixture', 'GITHUB_RUN_NUMBER': '12', 'GITHUB_RUN_ATTEMPT': '1'}
        with patch.dict(os.environ, environment), patch.object(sys, 'argv', ['images.py', 'publish']), \
             patch.dict(namespace['main'].__globals__, {'run': execute}):
            if fail:
                with self.assertRaisesRegex(RuntimeError, 'Registry'): namespace['main']()
            else:
                namespace['main']()
        return calls

    def test_latest_is_published_last_from_verified_digests(self):
        calls = self.exercise()
        latest = [i for i,c in enumerate(calls) if any(a.endswith(':latest') for a in c)]
        inspections = [i for i,c in enumerate(calls) if c[:4] == ('docker', 'buildx', 'imagetools', 'inspect')]
        self.assertTrue(latest)
        self.assertGreater(min(latest), max(inspections))
        for i in latest:
            self.assertIn('@sha256:', calls[i][-1])
        annotations = [a for c in calls for a in c if a.startswith('index:')]
        self.assertIn('index:io.valentemath.schema=0', annotations)
        self.assertIn('index:org.opencontainers.image.revision=' + 'a'*40, annotations)
        self.assertFalse(any(c[0] == 'gh' for c in calls))

    def test_registry_failure_never_advances_latest(self):
        calls = self.exercise(fail=True)
        self.assertFalse(any(a.endswith(':latest') for c in calls for a in c))


if __name__ == '__main__': unittest.main()
