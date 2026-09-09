#!/usr/bin/env python3
"""Disposable native Compose update drill; registry/discovery use local fixtures.

Requires prebuilt athenaeum:local, athenaeum-edge:local, quacktuaries:phase-e,
and age + age-keygen in ATHENAEUM_TEST_AGE's directory. Never uses .state/local.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
sys.path.insert(0, str(ROOT / 'tests'))
from athenaeum_ops.common import atomic_json, compose, load_config, operation_lock, run
from athenaeum_ops.delivery import Host, Updater
from test_delivery import Client, release


class LocalHost(Host):
    """Use locally built images in place of registry downloads, all else real."""
    def __init__(self, cfg, updates, mapping):
        super().__init__(cfg, updates)
        self.mapping = mapping
    def pull(self, descriptor):
        image = json.loads(run(['docker', 'image', 'inspect', self.mapping[descriptor['image']]]))[0]
        assert image['Architecture'] == self.arch
    def verify_current(self, service, descriptor):
        image = json.loads(run(['docker', 'image', 'inspect', self.mapping[descriptor['image']]]))[0]
        assert self.container(service)['Image'] == image['Id']
    def up(self, service=None):
        overlay = Path(self.cfg['data_root']) / 'deploy-state/images.json'
        model = json.loads(overlay.read_bytes())
        for entry in model['services'].values():
            entry['image'] = self.mapping.get(entry['image'], entry['image'])
        atomic_json(overlay, model)
        super().up(service)


def main():
    os.umask(0o077)
    age = Path(os.environ['ATHENAEUM_TEST_AGE'])
    with tempfile.TemporaryDirectory(prefix='athenaeum-delivery-drill-') as temp:
        root = Path(temp)
        for relative in ['data/apps/quacktuaries/data', 'data/edge/data', 'data/edge/config', 'data/backups/staging', 'data/deploy-state', 'state', 'objects']:
            (root / relative).mkdir(parents=True, mode=0o700)
        secret = root / 'secret'
        secret.write_text('synthetic-delivery-fixture-' + 'x' * 64)
        identity = root / 'identity'
        run([age.parent / 'age-keygen', '-o', identity])
        recipient = run([age.parent / 'age-keygen', '-y', identity]).decode().strip()
        cfg = json.loads((ROOT / 'deploy/recovery.example.json').read_bytes())
        cfg.update(mode='local', data_root=str(root / 'data'), filesystem_uuid='local', state_dir=str(root / 'state'),
            compose_project='athenaeum-delivery-drill', compose_files=[str(ROOT / 'compose.yaml'), str(ROOT / 'compose.local.yaml')],
            compose_env=str(root / 'compose.env'), session_secret=str(secret), recipient=recipient, age_binary=str(age),
            bucket_cap_bytes=20_000_000, max_snapshot_bytes=2_000_000, max_restore_bytes=5_000_000, min_free_bytes=1_000_000,
            store={'kind':'local','directory':str(root / 'objects'),'prefix':'athenaeum/v1/'})
        atomic_json(root / 'recovery.json', cfg)
        values={'DATA_ROOT':str(root / 'data'),'QUACKTUARIES_SECRET_FILE':str(secret), 'RUNTIME_UID':str(os.getuid()),'RUNTIME_GID':str(os.getgid()),
            'SITE_DOMAIN':'localhost','ACME_EMAIL':'fixture@example.invalid','EDGE_IMAGE':'athenaeum-edge:local','ATHENAEUM_IMAGE':'athenaeum:local',
            'QUACKTUARIES_IMAGE':'quacktuaries:phase-e','LOCAL_HTTP_PORT':'4484','LOCAL_HTTPS_PORT':'4485'}
        (root / 'compose.env').write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items()))
        client=Client()
        updates={'schema':1,'origin':'https://localhost:4485','ca_cert':None,'window':None,'readiness_seconds':25,'min_image_free_bytes':1_000_000,
            'services':{s:{'repository':d['repository'],'image':d['image'].split('@')[0],'automatic':s!='edge'} for s,d in client.releases.items()}}
        mapping={release(s)['image']:image for s,image in [('athenaeum','athenaeum:local'),('edge','athenaeum-edge:local'),('quacktuaries','quacktuaries:phase-e')]}
        for s in ('athenaeum','quacktuaries'):
            for generation in (2,3):
                tag=f'athenaeum-drill-{s}:{generation}'
                dockerfile=f'FROM {mapping[release(s)["image"]]}\nLABEL athenaeum.drill="{generation}"\n'
                if s=='athenaeum' and generation==3: dockerfile+='HEALTHCHECK --interval=1s --timeout=1s --retries=1 CMD exit 1\n'
                subprocess.run(['docker','build','-t',tag,'-'],input=dockerfile.encode(),check=True,stdout=subprocess.DEVNULL)
                mapping[release(s,generation)['image']]=tag
        cfg=load_config(root / 'recovery.json')
        host=LocalHost(cfg,updates,mapping)
        original_ready=host.ready
        def ready(service):
            # Caddy generates a private local CA after initial startup.
            ca=root / 'data/edge/data/caddy/pki/authorities/local/root.crt'
            import time
            deadline=time.monotonic()+20
            while not ca.exists() and time.monotonic()<deadline: time.sleep(.2)
            updates['ca_cert']=str(ca)
            original_ready(service)
        host.ready=ready
        try:
            with operation_lock(cfg):
                updater=Updater(cfg,updates,host,client)
                assert updater.run(initial=True)['status']=='initialized'
                edge_id=host.container('edge')['Id']
                quack_id=host.container('quacktuaries')['Id']
                client.releases['athenaeum']=release('athenaeum',2)
                assert updater.run()['services']['athenaeum']=='updated'
                assert host.container('edge')['Id']==edge_id and host.container('quacktuaries')['Id']==quack_id
                client.releases['athenaeum']=release('athenaeum',3)
                try: updater.run()
                except Exception as error:
                    assert 'previous compatible image restored' in str(error), str(error)
                else: raise AssertionError('Broken image accepted')
                assert updater.run()['services']['athenaeum']=='failed-digest-held'
                client.releases['quacktuaries']=release('quacktuaries',2)
                assert updater.run()['services']['quacktuaries']=='updated'
                assert host.container('edge')['Id']==edge_id
                assert list((root / 'objects/athenaeum/v1/commits').glob('*.json'))
                assert updater.rollback('quacktuaries')['status']=='rolled-back'
                print('PASS: real HTTPS initial deployment, one-service update, failed-health rollback, digest hold, encrypted pre-update backup, compatible app rollback')
        finally:
            compose(cfg,'down','--timeout','10')
            for s in ('athenaeum','quacktuaries'):
                for generation in (2,3):
                    run(['docker','image','rm',f'athenaeum-drill-{s}:{generation}'])


if __name__=='__main__':main()
