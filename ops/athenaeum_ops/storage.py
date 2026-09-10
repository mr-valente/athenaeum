"""Native OCI objects and a deliberately separate filesystem test backend."""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from .common import REPORT_OBJECT_NAME, Failure, directory, regular, digest


def report_name(name):
    if not isinstance(name, str) or not REPORT_OBJECT_NAME.fullmatch(name):
        raise Failure('Invalid report object name')
    return name


class LocalStore:
    def __init__(self, config):
        self.root = directory(config['directory'])

    def path(self, name):
        if not name or name.startswith('/') or any(p in ('.', '..', '') for p in name.split('/')):
            raise Failure('Invalid object name')
        p = self.root / name
        if any(x.is_symlink() for x in [p, *p.parents]):
            raise Failure('Symlink in local object store')
        return p

    def inventory(self):
        objects = []
        for p in self.root.rglob('*'):
            if p.is_symlink():
                raise Failure('Symlink in local object store')
            if p.is_file():
                objects.append({'name': p.relative_to(self.root).as_posix(), 'size': p.stat().st_size,
                                'etag': digest(p)})
        return objects, 0

    def put(self, name, source):
        dest = self.path(name)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(dest, 'xb') as out, open(source, 'rb') as inp:
            os.chmod(dest, 0o600)
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())

    def get(self, name, destination, maximum, etag=None):
        source = regular(self.path(name))
        if source.stat().st_size > maximum or (etag and digest(source) != etag):
            raise Failure('Object changed or exceeds download limit')
        with open(destination, 'xb') as out, open(source, 'rb') as inp:
            count = 0
            while chunk := inp.read(1024 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise Failure('Object exceeds download limit')
                out.write(chunk)

    def delete(self, name, etag):
        dest = regular(self.path(name))
        if digest(dest) != etag:
            raise Failure('Object changed; refusing retention deletion')
        dest.unlink()

    def publish(self, name, body):
        dest = self.path(report_name(name))
        fd, temp = tempfile.mkstemp(prefix='.publish-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(body)
            os.chmod(temp, 0o600)
            os.replace(temp, dest)
        finally:
            Path(temp).unlink(missing_ok=True)


class OCIStore:
    def __init__(self, config, client=None):
        self.namespace, self.bucket = config['namespace'], config['bucket']
        if client is None:
            import oci
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            client = oci.object_storage.ObjectStorageClient(
                {'region': config['region']}, signer=signer, timeout=(10, 60),
                retry_strategy=oci.retry.NoneRetryStrategy())
        self.client = client

    def inventory(self):
        c, ns, bucket = self.client, self.namespace, self.bucket
        info = c.get_bucket(ns, bucket).data
        # Disabled versioning means list_objects accounts for every stored version.
        # Suspended buckets can retain old versions, so refuse those too.
        if info.versioning != 'Disabled' or info.public_access_type != 'NoPublicAccess' or info.storage_tier != 'Standard':
            raise Failure('Backup bucket must be private Standard storage with versioning Disabled')
        objects, start = [], None
        while True:
            args = {'fields': 'name,size,etag', 'limit': 1000}
            if start:
                args['start'] = start
            page = c.list_objects(ns, bucket, **args).data
            objects.extend({'name': o.name, 'size': o.size, 'etag': o.etag} for o in page.objects)
            start = page.next_start_with
            if not start:
                break
        multipart_bytes = 0
        for upload in self.pages(c.list_multipart_uploads, ns, bucket):
            for part in self.pages(c.list_multipart_upload_parts, ns, bucket, upload.object, upload.upload_id):
                multipart_bytes += part.size
        return objects, multipart_bytes

    @staticmethod
    def pages(method, *args):
        page = None
        while True:
            response = method(*args, **({'page': page} if page else {}))
            yield from response.data
            page = response.headers.get('opc-next-page')
            if not page:
                break

    def put(self, name, source):
        with open(source, 'rb') as file:
            md5 = base64.b64encode(hashlib.file_digest(file, 'md5').digest()).decode()
            file.seek(0)
            self.client.put_object(self.namespace, self.bucket, name, file,
                                   content_length=Path(source).stat().st_size, content_md5=md5,
                                   if_none_match='*', content_type='application/octet-stream')

    def get(self, name, destination, maximum, etag=None):
        args = {'if_match': etag} if etag else {}
        response = self.client.get_object(self.namespace, self.bucket, name, **args)
        count = 0
        try:
            with open(destination, 'xb') as file:
                for chunk in response.data.raw.stream(1024 * 1024, decode_content=False):
                    count += len(chunk)
                    if count > maximum:
                        raise Failure('Object exceeds download limit')
                    file.write(chunk)
        finally:
            response.data.close()

    def delete(self, name, etag):
        self.client.delete_object(self.namespace, self.bucket, name, if_match=etag)

    def publish(self, name, body):
        """Overwrite the host report in place; unlike put, this object has no history to protect."""
        self.client.put_object(self.namespace, self.bucket, report_name(name), body,
                               content_length=len(body), content_type='application/json')


def open_store(cfg):
    return LocalStore(cfg['store']) if cfg['store']['kind'] == 'local' else OCIStore(cfg['store'])
