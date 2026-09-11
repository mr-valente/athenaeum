"""Daily Docker actions share the same lock and backup gates as the runbook."""
from .common import operation_lock, preflight, validate_compose, compose, compose_visible, run
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
        if command != 'deploy':
            say('Pulling the configured Docker Hub images; running containers stay in place...')
            docker(cfg, 'pull')
        if command == 'pull':
            say('PASS: images downloaded. No containers were replaced.')
            return
        say('Starting downloaded images and waiting up to 120 seconds for health...')
        docker(cfg, 'up', '--detach', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120')
        # Compose recreates a service when its definition changes, not when a file
        # it mounts does, and Git replaces sablier.yaml beneath the container's
        # single-file mount. Recreate Sablier every time so the checkout's policy,
        # theme and assets are the ones running. Apps stay up; Sablier adopts them
        # at the default session until verify renews each at its tier.
        say('Recreating Sablier so the checked-out policy, theme and assets are re-read...')
        docker(cfg, 'up', '--detach', '--no-build', '--pull', 'never', '--no-deps', '--force-recreate',
               '--wait', '--wait-timeout', '120', 'sablier')
        say(compose(cfg, 'ps').decode())
        say('PASS: containers healthy. Run athenaeumctl verify and check a classroom workflow.\nImage rollback is manual; persistent data has not been replaced.')
