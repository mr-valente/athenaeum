"""Exercise the restored app in a private, temporary container with no network."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from .archive import applications
from .common import APPS, Failure, regular, run

# Executed only in an operator-selected, already installed application image.
# Output is intentionally only pass/fail; no student/teacher values are logged.
PROBES = {'quacktuaries': r'''
import json,sqlite3,urllib.request
from pathlib import Path
with urllib.request.urlopen('http://127.0.0.1:8000/_health',timeout=3) as r:
 assert r.status==200 and json.load(r)['status']=='ok'
with urllib.request.urlopen('http://127.0.0.1:8000/',timeout=3) as r:
 assert b'Quacktuaries' in r.read()
with sqlite3.connect('file:/data/app.db?mode=ro',uri=True) as db:
 row=db.execute('SELECT id,status FROM sessions ORDER BY id LIMIT 1').fetchone()
 if row:
  with urllib.request.urlopen('http://127.0.0.1:8000/session/'+row[0]+'/state',timeout=3) as r:
   data=json.load(r)
   assert data['session_id']==row[0] and data['status']==row[1]
print('Restored application HTTP and saved session state verified.')
''', 'bernoulli': r'''
import json,sqlite3,urllib.request
from pathlib import Path
with urllib.request.urlopen('http://127.0.0.1:8000/_health',timeout=3) as r:
 assert r.status==200 and json.load(r)['status']=='ok'
with urllib.request.urlopen('http://127.0.0.1:8000/',timeout=3) as r:
 assert b'Bernoulli' in r.read()
with sqlite3.connect('file:/data/app.db?mode=ro',uri=True) as db:
 row=db.execute("SELECT id,status FROM sessions WHERE kind='flip_flop' ORDER BY id LIMIT 1").fetchone()
 if row:
  with urllib.request.urlopen('http://127.0.0.1:8000/x/flip-flop/s/'+row[0]+'/state',timeout=3) as r:
   data=json.load(r)
   assert data['session']['status']==row[1]
print('Restored application HTTP and saved session state verified.')
'''}


def select_images(values, manifest):
    """Map every restored database to an operator-chosen image: APP=REFERENCE.

    A bare reference is accepted only when the snapshot holds one database, so
    the documented single-app command keeps working for older recovery points.
    """
    testable = {app for app, entry in applications(manifest).items() if entry['database'] is not None}
    selected = {}
    for value in values:
        app, separator, reference = value.partition('=')
        if not separator:
            if len(testable) != 1:
                raise Failure('Use --image APP=REFERENCE once per restored application database')
            app, reference = next(iter(testable)), value
        if app not in APPS or app in selected:
            raise Failure(f'Unknown or repeated application in --image: {app}')
        selected[app] = reference
    if set(selected) != testable:
        raise Failure('Supply --image APP=REFERENCE for exactly these restored databases: ' + ', '.join(sorted(testable)))
    return selected


def test_runtime(target, image, manifest, app='quacktuaries'):
    # Operator supplies an immutable image; archive content never selects code to execute.
    if not re.fullmatch(r'(?:sha256:[a-f0-9]{64}|[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64})', image):
        raise Failure('Restore-test requires an explicitly supplied immutable image ID or repository digest')
    if applications(manifest).get(app, {}).get('database') is None:
        raise Failure(f'Snapshot holds no {app} database to test')
    info = json.loads(run(['docker', 'image', 'inspect', image]))[0]
    saved = manifest['images'].get(app)
    if saved is None:
        raise Failure(f'Snapshot recorded no running {app} image; choose a compatible version deliberately')
    if info['Id'] != saved['id'] and image.removeprefix('docker.io/') not in {d.removeprefix('docker.io/') for d in saved['digests']}:
        raise Failure('Supplied image does not match the snapshot; review compatibility before recovery')
    uid, gid = (10001, 10001) if os.geteuid() == 0 else (os.getuid(), os.getgid())
    with tempfile.TemporaryDirectory(prefix='.athenaeum-runtime-', dir=Path(target).parent) as temp:
        work = Path(temp)
        data = work / 'data'
        data.mkdir(mode=0o700)
        shutil.copyfile(Path(target) / 'apps' / app / 'app.db', data / 'app.db')
        secret = work / 'session-secret'
        shutil.copyfile(Path(target) / 'secrets' / (app + '-session-secret'), secret)
        for file in [data / 'app.db', secret]:
            os.chmod(file, 0o600)
        if os.geteuid() == 0:
            for file in [work, data, data / 'app.db', secret]:
                os.chown(file, uid, gid)
        name = 'athenaeum-restore-' + uuid.uuid4().hex[:12]
        args = ['docker', 'run', '--detach', '--name', name, '--pull', 'never', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--user', f'{uid}:{gid}', '--memory', '512m', '--cpus', '1',
                '--tmpfs', '/tmp:size=16777216,mode=1777,noexec,nosuid,nodev',
                '--mount', f'type=bind,src={data},dst=/data',
                '--mount', f'type=bind,src={secret},dst=/run/secrets/session,readonly',
                '--env', 'APP_ENV=production', '--env', 'ROOT_PATH=' + APPS[app]['root_path'],
                '--env', 'DB_PATH=/data/app.db', '--env', 'SESSION_SECRET_FILE=/run/secrets/session', image]
        try:
            run(args)
            deadline = time.monotonic() + 45
            while True:
                try:
                    run(['docker', 'exec', name, 'python', '-c', PROBES[app]], timeout=5)
                    break
                except Failure:
                    if time.monotonic() > deadline:
                        raise Failure('Restored app did not pass bounded startup/HTTP verification') from None
                    time.sleep(0.5)
        finally:
            # The name is generated here, never accepted from a snapshot.
            run(['docker', 'rm', '--force', name])
    return {'runtime': 'passed', 'network': 'none', 'image': image, 'application': app}
