"""Docker Hub discovery and local image compatibility checks."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from .common import Failure, atomic_json, regular, compose, run

SERVICES = ('athenaeum', 'quacktuaries', 'edge')
DIGEST = r'sha256:[a-f0-9]{64}'
REPOSITORY = r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*'
IMAGE = r'docker\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_.-]*'
VERSION = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}'


def json_data(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Failure('Duplicate JSON field')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError):
        raise Failure('Invalid JSON data') from None


def load_updates(cfg):
    """Compose is the image allowlist; there is no second service catalog."""
    model = json_data(compose(cfg, 'config', '--format', 'json'))
    edge = model['services']['edge']['environment']
    domain = edge['SITE_DOMAIN']
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', domain):
        raise Failure('Invalid SITE_DOMAIN')
    services = {}
    for name in SERVICES:
        image = model['services'][name]['image'].split('@')[0].split(':')[0]
        if not image.startswith('docker.io/'):
            image = 'docker.io/' + image
        if not re.fullmatch(IMAGE, image):
            raise Failure('Use a Docker Hub owner/image for ' + name)
        services[name] = {'image': image, 'automatic': name != 'edge'}
    local = cfg['mode'] == 'local'
    return {'services': services, 'origin': 'https://' + domain + (':' + edge.get('LOCAL_HTTPS_PORT', '8443') if local else ''),
            'ca_cert': str(Path(cfg['data_root']) / 'edge/data/caddy/pki/authorities/local/root.crt') if local else None,
            'readiness_seconds': 90, 'min_image_free_bytes': cfg['min_free_bytes']}


def validate_descriptor(value, service, allowed):
    try:
        fields = {'schema', 'service', 'repository', 'source_commit', 'version', 'image', 'style_version',
                  'compatibility', 'migration', 'created_at'}
        if set(value) != fields or type(value['schema']) is not int or value['schema'] != 1:
            raise ValueError()
        if value['service'] != service or not re.fullmatch(REPOSITORY, value['repository']):
            raise ValueError()
        if 'repository' in allowed and value['repository'] != allowed['repository']:
            raise ValueError()
        if not re.fullmatch(re.escape(allowed['image']) + '@' + DIGEST, value['image']):
            raise ValueError()
        if not re.fullmatch(r'[a-f0-9]{40}', value['source_commit']) or not re.fullmatch(VERSION, value['version']):
            raise ValueError()
        if not re.fullmatch(VERSION, value['style_version']):
            raise ValueError()
        c = value['compatibility']
        if set(c) != {'config', 'read_min', 'read_max', 'write'} or any(type(v) is not int or v < 0 for v in c.values()):
            raise ValueError()
        if c['config'] != 1 or not c['read_min'] <= c['write'] <= c['read_max']:
            raise ValueError()
        if value['migration'] not in ('none', 'backward-compatible', 'destructive'):
            raise ValueError()
        created = datetime.fromisoformat(value['created_at'])
        if created.tzinfo is None or created > datetime.now(timezone.utc):
            raise ValueError()
        if service != 'quacktuaries' and (c != {'config': 1, 'read_min': 0, 'read_max': 0, 'write': 0} or value['migration'] != 'none'):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Failure('Release descriptor violates the local service/image/compatibility contract') from None
    return value


def compatible(previous, candidate, actual_schema):
    old, new = previous['compatibility'], candidate['compatibility']
    if candidate['migration'] == 'destructive':
        raise Failure('Destructive migration requires a separate authorized maintenance operation')
    if not new['read_min'] <= actual_schema <= new['read_max'] or new['write'] < actual_schema:
        raise Failure('Candidate cannot read the current data schema')
    if candidate['migration'] == 'none' and new['write'] != actual_schema:
        raise Failure('Schema change requires an explicit migration declaration')
    if not old['read_min'] <= new['write'] <= old['read_max']:
        raise Failure('Previous image cannot read the resulting schema; automatic rollback is unsafe')


class ReleaseClient:
    """Read the approved Docker Hub :latest index, then deploy its fixed digest.

    Metadata travels with the image. Docker handles registry auth and HTTPS.
    A small persisted retry timestamp prevents hammering a failing registry.
    """
    def __init__(self, cfg):
        self.path = Path(cfg['state_dir']) / 'release-cache.json'
        self.cache = json_data(regular(self.path, private=True).read_bytes()) if self.path.exists() else {}

    def fetch(self, service, allowed):
        image = allowed['image']
        entry = self.cache.setdefault(image, {})
        if entry.get('next_attempt', 0) > time.time():
            return None
        try:
            manifest = json_data(run(['docker', 'buildx', 'imagetools', 'inspect', image + ':latest',
                                      '--format', '{{json .Manifest}}'], timeout=60))
            if not re.fullmatch(DIGEST, manifest['digest']):
                raise Failure('Registry returned an invalid digest')
            platforms = {(m.get('platform', {}).get('os'), m.get('platform', {}).get('architecture'))
                         for m in manifest.get('manifests', [])}
            if not {('linux', 'amd64'), ('linux', 'arm64')} <= platforms:
                raise Failure('Image must include native AMD64 and ARM64 builds')
            # This response contains both the index digest and its annotations.
            a = manifest['annotations']
            schema = a['io.valentemath.schema']
            if not re.fullmatch(r'[0-9]{1,6}', schema):
                raise Failure('Invalid image schema version')
            schema = int(schema)
            value = {'schema': 1, 'service': service,
                     'repository': a['org.opencontainers.image.source'].removeprefix('https://github.com/'),
                     'source_commit': a['org.opencontainers.image.revision'],
                     'version': a['org.opencontainers.image.version'], 'image': image + '@' + manifest['digest'],
                     'style_version': a['io.valentemath.style'],
                     'compatibility': {'config': 1, 'read_min': schema, 'read_max': schema, 'write': schema},
                     'migration': 'none', 'created_at': a['org.opencontainers.image.created']}
            validate_descriptor(value, service, allowed)
            entry.update(failures=0, next_attempt=time.time() + 300, error=None)
            return value
        except Exception as error:
            failures = min(entry.get('failures', 0) + 1, 8)
            message = str(error) if isinstance(error, Failure) else 'Image metadata missing or invalid; check CI publication'
            entry.update(failures=failures, next_attempt=time.time() + min(300 * 2 ** (failures - 1), 3600), error=message)
            raise Failure(message) from None
        finally:
            atomic_json(self.path, self.cache)
