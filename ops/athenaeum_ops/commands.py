"""Small command-center vocabulary over existing deployment/recovery operations.

Commands parse before any privileged host work. Repository sync, image pulls,
container replacement and tool installation stay explicit separate actions.
"""
import argparse
import subprocess
import sys

from .common import SERVICES, Failure, compose_visible, operation_lock
from .repository import STACK, repo_status, sync


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('Use a positive line count.')
    return number


def add_commands(sub):
    docker = sub.add_parser('docker', help='Pull images, deploy safely, inspect containers/logs')
    actions = docker.add_subparsers(dest='docker_command', required=True)
    pull = actions.add_parser('pull', help='Download images; optionally back up and deploy')
    pull.add_argument('--deploy', action='store_true', help='Verified backup, then pull and replace containers')
    actions.add_parser('deploy', help='Back up and apply Compose using already downloaded images')
    actions.add_parser('ps', help='List containers and health')
    actions.add_parser('images', help='Show configured image references')
    logs = actions.add_parser('logs', help='Read container logs (Ctrl-C ends following)')
    logs.add_argument('service', nargs='?', choices=SERVICES)
    logs.add_argument('--tail', type=positive, default=100)
    logs.add_argument('--follow', '-f', action='store_true')
    repo = sub.add_parser('repo', help='Manage the VM Git checkout; never deploys containers')
    actions = repo.add_subparsers(dest='repo_command', required=True)
    actions.add_parser('status', help='Show local branch, commit and edits')
    actions.add_parser('sync', help='Fetch main, validate Compose, fast-forward a clean checkout')
    sub.add_parser('verify', help='Verify public HTTPS, container health and metadata isolation')
    tools = sub.add_parser('self', help='Maintain the installed host command tools')
    actions = tools.add_subparsers(dest='self_command', required=True)
    actions.add_parser('update', help='Install tools from the VM checkout; no Docker restart')


def dispatch(cfg, args):
    # Imported lazily so recovery cli and runbook wrappers can share functions
    # without circular module initialization or loading OCI just for --help.
    if args.command == 'docker':
        from .management import operate
        action = args.docker_command
        if action in ('pull', 'deploy'):
            command = 'update' if action == 'pull' and args.deploy else action
            operate(cfg, command, visible=True)
        elif action == 'logs':
            # A log follower must not hold the backup lock indefinitely.
            options = ['logs', '--tail', str(args.tail)]
            if args.follow:
                options.append('--follow')
            if args.service:
                options.append(args.service)
            compose_visible(cfg, *options)
        else:
            with operation_lock(cfg):
                compose_visible(cfg, *(['ps', '--all'] if action == 'ps' else ['config', '--images']))
    elif args.command == 'verify':
        from .verification import verify
        with operation_lock(cfg):
            verify(cfg)
    elif args.command == 'repo':
        if cfg['mode'] != 'production':
            raise Failure('Repository commands are for the installed production VM.')
        if args.repo_command == 'status':
            with operation_lock(cfg):
                repo_status()
        else:
            sync(cfg)
    elif args.command == 'self':
        if cfg['mode'] != 'production':
            raise Failure('Tool installation is for the production VM.')
        print('Installing host tools from the current checkout; Docker will keep running.', flush=True)
        # install-host obtains its own operation lock. It does not restart
        # Docker or enable timers, unlike first-time runbook/install-services.
        result = subprocess.run([sys.executable, str(STACK / 'ops/install-host'),
                                 '--config', cfg['_source'], '--apply'])
        if result.returncode:
            raise Failure('Host tool installation failed; inspect the reported stage and private log.')
    else:
        return False
    return True
