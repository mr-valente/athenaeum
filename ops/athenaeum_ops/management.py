"""Daily Docker actions share the same lock and backup gates as the runbook."""
from pathlib import Path
from .common import operation_lock, preflight, validate_compose, compose, compose_visible, run, Failure
from .cli import recorded_backup


def say(message):
    print(message, flush=True)


def operate(cfg, command, visible=False):
    docker = compose_visible if visible else compose
    # One lock spans the backup and container replacement, so the hourly backup
    # cannot capture half of a manual update. This is the existing recovery lock,
    # not another daemon, deployment queue, or release-state system.
    with operation_lock(cfg):
        say('Checking the data mount, configuration and metadata guard...')
        preflight(cfg)
        validate_compose(cfg)
        run(['systemctl', 'is-active', '--quiet', 'athenaeum-metadata-guard.service'])
        if command in ('update', 'deploy'):
            say('Backing up the current database, signing key, configuration and image references...')
            result = recorded_backup(cfg)
            say('Verified recovery point: ' + result['snapshot'])
        elif command == 'start':
            # start is only for an empty first install or an idempotent retry
            # before the app creates its database. Existing records require the
            # update path and its pre-update backup, even after failed startup.
            if any((Path(cfg['data_root']) / 'apps/quacktuaries/data').iterdir()):
                raise Failure('Existing app data: use stack update so a backup precedes replacement.')
        if command != 'deploy':
            say('Pulling the configured Docker Hub images; running containers stay in place...')
            docker(cfg, 'pull')
        if command == 'pull':
            say('PASS: images downloaded. No containers were replaced.')
            return
        say('Starting downloaded images and waiting up to 120 seconds for health...')
        docker(cfg, 'up', '--detach', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120')
        say(compose(cfg, 'ps').decode())
        say('PASS: containers healthy. Run athenaeumctl verify and check a classroom workflow.\nImage rollback is manual; persistent data has not been replaced.')
