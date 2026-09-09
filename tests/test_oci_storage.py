"""OCI adapter contract tests; fake transport never contacts a tenancy."""
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from athenaeum_ops.common import Failure
from athenaeum_ops.storage import OCIStore


class Client:
    def __init__(self):
        self.calls = []
        self.versioning = 'Disabled'

    def get_bucket(self, *args):
        return NS(data=NS(versioning=self.versioning, public_access_type='NoPublicAccess', storage_tier='Standard'))

    def list_objects(self, *args, **kw):
        self.calls.append(('objects', kw))
        if 'start' not in kw:
            return NS(data=NS(objects=[NS(name='unrelated/file', size=300, etag='u')], next_start_with='next'))
        return NS(data=NS(objects=[NS(name='athenaeum/v1/file', size=20, etag='a')], next_start_with=None))

    def list_multipart_uploads(self, *args, **kw):
        self.calls.append(('uploads', kw))
        return NS(data=[NS(object='unrelated/multipart', upload_id='upload')], headers={})

    def list_multipart_upload_parts(self, *args, **kw):
        self.calls.append(('parts', kw))
        if not kw:
            return NS(data=[NS(size=50)], headers={'opc-next-page': 'part2'})
        return NS(data=[NS(size=60)], headers={})

    def put_object(self, ns, bucket, name, data, **kw):
        self.put = (name, data.read(), kw)

    def get_object(self, ns, bucket, name, **kw):
        self.get = (name, kw)
        return NS(data=NS(raw=NS(stream=lambda *a, **k: iter([b'abc', b'def'])), close=lambda: None))

    def delete_object(self, ns, bucket, name, **kw):
        self.delete = (name, kw)


class OracleAdapterTests(unittest.TestCase):
    def test_all_objects_and_multipart_parts_count_including_unrelated_prefixes(self):
        client = Client()
        store = OCIStore({'namespace': 'test', 'bucket': 'test'}, client)
        objects, multipart = store.inventory()
        self.assertEqual(sum(o['size'] for o in objects) + multipart, 430)
        self.assertEqual(client.calls[-1], ('parts', {'page': 'part2'}))
        self.assertFalse(any('prefix' in kw for _, kw in client.calls))
        client.versioning = 'Suspended'
        with self.assertRaises(Failure):
            store.inventory()

    def test_conditional_writes_deletes_and_bounded_download(self):
        client = Client()
        store = OCIStore({'namespace': 'test', 'bucket': 'test'}, client)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'data'
            source.write_bytes(b'abc')
            store.put('name', source)
            self.assertEqual(client.put[2]['if_none_match'], '*')
            self.assertEqual(client.put[2]['content_length'], 3)
            self.assertEqual(client.put[2]['content_md5'], 'kAFQmDzST7DWlj99KOF/cg==')
            store.get('name', root / 'download', 10, 'etag')
            self.assertEqual((root / 'download').read_bytes(), b'abcdef')
            self.assertEqual(client.get[1], {'if_match': 'etag'})
            with self.assertRaises(Failure):
                store.get('name', root / 'too-big', 3)
            store.delete('name', 'etag')
            self.assertEqual(client.delete[1], {'if_match': 'etag'})
