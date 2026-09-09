"""Host-owned image updates. Call under the shared operations lock."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from urllib.parse import urlsplit

from .archive import inspect_database
from .common import Failure, atomic_json, compose, directory, guard_mount, preflight, regular, run, validate_compose
from .recovery import backup
from .releases import ReleaseClient, compatible, validate_descriptor


def read_state(path, default):
    return json.loads(regular(path, private=True).read_bytes()) if path.exists() else default


def pause(cfg, paused):
    atomic_json(Path(cfg['state_dir']) / 'updates-pause.json', {'paused': paused, 'at': time.time()})
    return {'updates_paused': paused}


def paused(cfg):
    return read_state(Path(cfg['state_dir']) / 'updates-pause.json', {'paused': False})['paused']


class Host:
    def __init__(self, cfg, updates):
        self.cfg, self.updates = cfg, updates

    def preflight(self):
        preflight(self.cfg)
        validate_compose(self.cfg)
        if self.cfg['mode'] == 'production':
            run(['systemctl', 'is-active', '--quiet', 'athenaeum-metadata-guard.service'])
        info = json.loads(run(['docker', 'info', '--format', '{{json .}}']))
        if info['OSType'] != 'linux':
            raise Failure('Linux Docker engine required')
        arch = {'x86_64': 'amd64', 'aarch64': 'arm64', 'amd64': 'amd64', 'arm64': 'arm64'}.get(info['Architecture'])
        if arch is None:
            raise Failure('Unsupported Docker architecture')
        self.arch = arch
        if shutil.disk_usage(info['DockerRootDir']).free < self.updates['min_image_free_bytes']:
            raise Failure('Insufficient space on the Docker image filesystem')

    def container(self, service):
        ids = compose(self.cfg, 'ps', '--all', '--quiet', service).decode().split()
        if len(ids) != 1:
            raise Failure('Expected exactly one container for ' + service)
        return json.loads(run(['docker', 'inspect', ids[0]]))[0]

    def verify_current(self, service, descriptor):
        image = json.loads(run(['docker', 'image', 'inspect', descriptor['image']]))[0]
        if self.container(service)['Image'] != image['Id']:
            raise Failure('Running image differs from recorded release; review manual deployment drift')

    def empty(self):
        if compose(self.cfg, 'ps', '--all', '--quiet').strip():
            raise Failure('Initial deployment requires no existing project containers')
        if any((Path(self.cfg['data_root']) / 'apps/quacktuaries/data').iterdir()):
            raise Failure('Initial deployment requires empty app data; use a deliberate migration for existing data')

    def pull(self, descriptor):
        reference = descriptor['image']
        manifest = json.loads(run(['docker', 'buildx', 'imagetools', 'inspect', '--raw', reference], timeout=60))
        platforms = {(m.get('platform', {}).get('os'), m.get('platform', {}).get('architecture')) for m in manifest.get('manifests', [])}
        if not {('linux', 'amd64'), ('linux', 'arm64')} <= platforms:
            raise Failure('Release is missing required AMD64/ARM64 index manifests')
        run(['docker', 'pull', '--platform', 'linux/' + self.arch, reference], timeout=600)
        image = json.loads(run(['docker', 'image', 'inspect', reference]))[0]
        digests = {d.removeprefix('docker.io/') for d in image.get('RepoDigests', [])}
        if (reference.removeprefix('docker.io/') not in digests or image['Architecture'] != self.arch or image['Os'] != 'linux'):
            raise Failure('Pulled image digest or native architecture does not match')
        self.preflight()  # Preserve disk headroom after download too.

    def activity(self, command='status'):
        container = self.container('quacktuaries')
        if command == 'drain' and not container['State'].get('Running'):
            # Stop Docker restart attempts before reverting a crashed candidate.
            run(['docker', 'stop', container['Id']])
            return {'protocol': 1, 'active_class': False, 'data_schema': self.database()['user_version'], 'held': True}
        result = json.loads(run(['docker', 'exec', container['Id'], 'python', '-m', 'app.operations', command], timeout=30))
        if (set(result) != {'protocol', 'active_class', 'data_schema', 'held'} or result['protocol'] != 1
                or type(result['active_class']) is not bool or type(result['held']) is not bool
                or type(result['data_schema']) is not int):
            raise Failure('Invalid private class activity response')
        return result

    def database(self):
        return inspect_database(Path(self.cfg['data_root']) / 'apps/quacktuaries/data/app.db')

    def backup(self):
        status_path = Path(self.cfg['state_dir']) / 'status.json'
        state = read_state(status_path, {})
        state['last_attempt'] = {'at': time.time(), 'status': 'running'}
        atomic_json(status_path, state)
        try:
            result = backup(self.cfg)
        except BaseException:
            state['last_attempt'] = {'at': time.time(), 'status': 'failed', 'error': 'Pre-deployment backup failed'}
            atomic_json(status_path, state)
            raise
        state.update(last_backup=result, last_attempt={'at': time.time(), 'status': 'ok'})
        atomic_json(status_path, state)
        return result

    def up(self, service=None):
        guard_mount(self.cfg)
        args = ['up', '--detach', '--no-build', '--pull', 'never']
        if service:
            args += ['--no-deps', '--force-recreate', service]
        compose(self.cfg, *args)

    def stop_initial(self):
        compose(self.cfg, 'stop', 'edge', 'athenaeum', 'quacktuaries')

    def remove_image(self, reference):
        # Never force-remove images: Docker also protects references in use.
        run(['docker', 'image', 'rm', reference])

    def ready(self, service):
        deadline = time.monotonic() + self.updates['readiness_seconds']
        while True:
            try:
                state = self.container(service)['State']
                if not state.get('Running') or state.get('Health', {}).get('Status') != 'healthy':
                    raise Failure('Container not healthy')
                for path in (['/', '/quacktuaries/'] if service == 'edge' else ['/quacktuaries/' if service == 'quacktuaries' else '/']):
                    origin = urlsplit(self.updates['origin'])
                    args = ['curl', '--fail', '--silent', '--show-error', '--max-time', '5', '--noproxy', '*',
                            '--resolve', f'{origin.hostname}:{origin.port or 443}:127.0.0.1', '--output', '/dev/null', '--write-out', '%{http_code}']
                    if self.updates['ca_cert']:
                        args += ['--cacert', self.updates['ca_cert']]
                    if run(args + [self.updates['origin'] + path], timeout=6).strip() != b'200':
                        raise Failure('Public prefix must return HTTP 200 without redirecting elsewhere')
                return
            except Failure:
                if time.monotonic() >= deadline:
                    raise Failure('Release failed bounded container and HTTPS prefix readiness') from None
                time.sleep(1)


class Updater:
    def __init__(self, cfg, updates, host=None, client=None):
        self.cfg, self.updates = cfg, updates
        self.host = host or Host(cfg, updates)
        self.client = client or ReleaseClient(cfg)
        guard_mount(cfg)
        self.root = directory(Path(cfg['data_root']) / 'deploy-state')
        self.path = self.root / 'releases.json'
        self.state = read_state(self.path, {'schema': 1, 'services': {}, 'transaction': None})
        self.overlay = self.root / 'images.json'

    def save(self):
        atomic_json(self.path, self.state)

    def cleanup_images(self):
        protected = {entry[key]['image'] for entry in self.state['services'].values()
                     for key in ('current', 'previous') if entry.get(key)}
        pending = self.state.setdefault('obsolete_images', [])
        retained = []
        for index, reference in enumerate(pending):
            if reference in protected:
                continue
            if index >= 5:
                retained.append(reference)
                continue
            try:
                self.host.remove_image(reference)
            except Failure:
                retained.append(reference)
        self.state['obsolete_images'] = retained
        self.save()

    def images(self, replacements=None):
        images = {s: {'image': entry['current']['image']} for s, entry in self.state['services'].items()}
        images.update({s: {'image': d['image']} for s, d in (replacements or {}).items()})
        guard_mount(self.cfg)
        atomic_json(self.overlay, {'services': images})

    def run(self, initial=False, service=None):
        if paused(self.cfg):
            return {'status': 'paused'}
        if self.state['transaction']:
            raise Failure('Interrupted deployment is held; inspect status and use an explicit compatible rollback')
        self.host.preflight()
        if initial:
            return self.initial()
        if set(self.state['services']) != set(self.updates['services']):
            raise Failure('Initialize reviewed releases with deploy --initial first')
        outcomes = {}
        for name, allowed in self.updates['services'].items():
            if (service and service != name) or (not service and not allowed['automatic']):
                continue
            try:
                current = self.state['services'][name]['current']
                validate_descriptor(current, name, allowed)
                candidate = self.client.fetch(name, allowed)
                if candidate is None:
                    outcomes[name] = 'discovery-backoff'
                    continue
                validate_descriptor(candidate, name, allowed)
                if candidate['image'] == current['image']:
                    outcomes[name] = 'current'
                elif candidate['image'] in self.state['services'][name].get('failed', {}):
                    outcomes[name] = 'failed-digest-held'
                elif self.state['services'][name].get('pull_retry', {}).get('image') == candidate['image'] and self.state['services'][name]['pull_retry']['next_attempt'] > time.time():
                    outcomes[name] = 'registry-backoff'
                elif datetime.fromisoformat(candidate['created_at']) <= datetime.fromisoformat(current['created_at']):
                    outcomes[name] = 'older-release-held'
                else:
                    outcomes[name] = self.change(name, candidate)
            except Failure as error:
                outcomes[name] = str(error)
                self.state['last_error'] = {'service': name, 'error': str(error), 'at': time.time()}
                self.save()
                raise
        self.state['last_poll'] = {'at': time.time(), 'results': outcomes}
        self.state.pop('last_error', None)
        self.save()
        self.cleanup_images()
        return {'status': 'checked', 'services': outcomes}

    def initial(self):
        if self.state['services']:
            raise Failure('Initial deployment already recorded')
        self.host.empty()
        releases = {}
        for name, allowed in self.updates['services'].items():
            descriptor = self.client.fetch(name, allowed)
            if descriptor is None:
                raise Failure('Initial release discovery is in backoff; retry after its interval')
            releases[name] = validate_descriptor(descriptor, name, allowed)
            if descriptor['migration'] != 'none' or descriptor['compatibility']['write'] != 0:
                raise Failure('Initial release must implement the baseline schema without migration')
        for descriptor in releases.values():
            self.host.pull(descriptor)
        self.state['transaction'] = {'kind': 'initial', 'candidates': releases, 'at': time.time()}
        self.save()
        self.images(releases)
        try:
            self.host.up()
            for name in releases:
                self.host.ready(name)
            self.host.activity()
            if self.host.database()['user_version'] != 0:
                raise Failure('Initial database schema differs from the release')
        except BaseException:
            self.host.stop_initial()
            raise
        self.state['services'] = {s: {'current': d, 'previous': None, 'failed': {}} for s, d in releases.items()}
        self.state['transaction'] = None
        self.save()
        return {'status': 'initialized', 'services': list(releases)}

    def change(self, name, candidate, rollback=False):
        entry = self.state['services'][name]
        previous = entry['current']
        self.host.verify_current(name, previous)
        gate = name in ('quacktuaries', 'edge')
        if gate and self.host.activity()['active_class']:
            return 'active-class-deferred'
        before = self.host.database() if name == 'quacktuaries' else None
        actual = before['user_version'] if before else 0
        if rollback:
            c = candidate['compatibility']
            if not c['read_min'] <= actual <= c['read_max']:
                raise Failure('Rollback image cannot read live schema; no database overwrite performed')
        else:
            compatible(previous, candidate, actual)
        try:
            self.host.pull(candidate)
        except Failure:
            retry = entry.get('pull_retry', {})
            count = min(retry.get('failures', 0) + 1, 8) if retry.get('image') == candidate['image'] else 1
            entry['pull_retry'] = {'image': candidate['image'], 'failures': count,
                'next_attempt': time.time() + min(300 * 2 ** (count - 1), 3600)}
            self.save()
            raise
        entry.pop('pull_retry', None)
        if gate:
            gate_state = self.host.activity('drain')
            if gate_state['active_class'] or not gate_state['held']:
                return 'active-class-deferred'
        try:
            # Held requests prevent a class from starting between backup and replacement.
            if name == 'quacktuaries':
                before = self.host.database()
                self.host.backup()
            self.state['transaction'] = {'kind': 'update', 'service': name, 'previous': previous,
                'candidate': candidate, 'database_before': before, 'at': time.time()}
            self.save()
            self.images({name: candidate})
            try:
                self.host.up(name)
                if gate:
                    self.host.activity('release')
                self.host.ready(name)
                if before:
                    after = self.host.database()
                    if after['user_version'] != (actual if rollback else candidate['compatibility']['write']):
                        raise Failure('Runtime schema differs from the release declaration')
                    if candidate['migration'] == 'none' and after['schema_sha256'] != before['schema_sha256']:
                        raise Failure('Undeclared schema change; operator review required')
            except BaseException as error:
                entry.setdefault('failed', {})[candidate['image']] = {'at': time.time(), 'reason': 'Startup/readiness/schema failure'}
                self.save()
                self.recover_transaction()
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise Failure('Candidate failed; previous compatible image restored and failed digest held') from None
            obsolete = entry.get('previous')
            if obsolete and obsolete['image'] not in (previous['image'], candidate['image']):
                pending = self.state.setdefault('obsolete_images', [])
                if obsolete['image'] not in pending:
                    pending.append(obsolete['image'])
            entry['previous'], entry['current'] = previous, candidate
            if rollback:
                entry.setdefault('failed', {})[previous['image']] = {'at': time.time(), 'reason': 'Operator rollback'}
            self.state['transaction'] = None
            self.save()
            return 'rolled-back' if rollback else 'updated'
        finally:
            if gate:
                # If replacement failed, release the surviving container's transient hold.
                self.host.activity('release')

    def recover_transaction(self):
        tx = self.state['transaction']
        if not tx or tx['kind'] != 'update':
            raise Failure('No prior compatible image for this interrupted initial deployment; manual recovery required')
        name, previous = tx['service'], tx['previous']
        if name in ('quacktuaries', 'edge'):
            state = self.host.activity('drain')
            if state['active_class'] or not state['held']:
                raise Failure('Rollback held because a class is active; preserve the transaction for operator recovery')
        if tx['database_before']:
            after = self.host.database()
            c = previous['compatibility']
            if not c['read_min'] <= after['user_version'] <= c['read_max']:
                raise Failure('Rollback cannot read the live schema; no automatic data restore')
            if tx['candidate']['migration'] == 'none' and after['schema_sha256'] != tx['database_before']['schema_sha256']:
                raise Failure('Unexpected schema mutation; rollback held for manual recovery')
        self.images({name: previous})
        self.host.up(name)
        if name in ('quacktuaries', 'edge'):
            self.host.activity('release')
        self.host.ready(name)
        self.state['services'][name].setdefault('failed', {})[tx['candidate']['image']] = {'at': time.time(), 'reason': 'Failed or interrupted deployment'}
        self.state['transaction'] = None
        self.save()

    def rollback(self, name):
        self.host.preflight()
        if self.state['transaction']:
            if self.state['transaction'].get('service') != name:
                raise Failure('Requested app does not match the interrupted deployment')
            try:
                self.recover_transaction()
            finally:
                if name in ('quacktuaries', 'edge'):
                    self.host.activity('release')
            return {'status': 'recovered', 'service': name}
        previous = self.state['services'].get(name, {}).get('previous')
        if previous is None:
            raise Failure('No previous release recorded for this app')
        validate_descriptor(previous, name, self.updates['services'][name])
        return {'status': self.change(name, previous, rollback=True), 'service': name}


def delivery_status(cfg):
    result = {'paused': paused(cfg)}
    try:
        guard_mount(cfg)
        result['releases'] = read_state(Path(cfg['data_root']) / 'deploy-state/releases.json', {})
        result['containers'] = []
        raw = compose(cfg, 'ps', '--all', '--format', 'json').decode().strip()
        containers = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
        for c in containers:
            result['containers'].append({k: c.get(k) for k in ('Service', 'Image', 'State', 'Health')})
        if cfg['mode'] == 'production':
            result['timers'] = {}
            for timer in ('athenaeum-backup.timer', 'athenaeum-updates.timer'):
                result['timers'][timer] = run(['systemctl', 'show', timer, '--property=ActiveState,NextElapseUSecRealtime', '--value']).decode().splitlines()
    except Failure as error:
        result['error'] = str(error)
    result['discovery'] = {k: {f: e.get(f) for f in ('next_attempt', 'failures', 'error')} for k, e in
                          read_state(Path(cfg['state_dir']) / 'release-cache.json', {}).items()}
    return result
