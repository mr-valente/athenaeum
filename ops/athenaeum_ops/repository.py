"""Maintain the VM source checkout without touching running containers.

Git runs as the checkout owner, even though the command center runs as root.
This preserves the administrator's ability to inspect/edit the checkout and
avoids root-owned Git files or global safe.directory exceptions. The host lock
keeps a backup/deploy from reading files halfway through a source update.
"""
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile

from .common import Failure, directory, operation_lock, validate_compose

STACK = Path('/opt/athenaeum/stack')
REMOTE = 'https://github.com/mr-valente/athenaeum.git'
BRANCH = 'main'


def git(root, *args):
    owner = pwd.getpwuid(root.stat().st_uid)
    # Ignore inherited Git overrides; use the checkout owner's credentials and
    # HOME. Never execute checkout hooks during an administrative source sync.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(HOME=owner.pw_dir, USER=owner.pw_name, LOGNAME=owner.pw_name,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    options = {}
    if os.geteuid() == 0:
        options = {'user': owner.pw_uid, 'group': owner.pw_gid,
                   'extra_groups': os.getgrouplist(owner.pw_name, owner.pw_gid)}
    elif os.geteuid() != owner.pw_uid:
        raise Failure('Run repository maintenance as the checkout owner or through athenaeumctl.')
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-C', str(root), *args]
    try:
        result = subprocess.run(command, env=env, capture_output=True, timeout=180,
                                umask=0o022, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('Git could not finish; check Git installation and GitHub connectivity.') from None
    if result.returncode:
        # Git can echo credential-bearing URLs. Report the stage, not stderr.
        raise Failure(f'Git {args[0]} failed; inspect the checkout as its owner and check GitHub access.')
    return result.stdout.decode().strip()


def checkout(root, require_main=True):
    directory(root)
    if not (root / '.git').exists():
        raise Failure('Expected a Git checkout at /opt/athenaeum/stack; follow the host setup guide.')
    if Path(git(root, 'rev-parse', '--show-toplevel')) != root:
        raise Failure('Expected the stack directory itself to be the Git checkout.')
    if not require_main:
        return
    if git(root, 'remote', 'get-url', 'origin') != REMOTE:
        raise Failure(f'Expected origin to be {REMOTE}; inspect the remote before syncing.')
    if git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD') != BRANCH:
        raise Failure('Repository sync requires the main branch; preserve other work before switching.')


def require_clean(root):
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise Failure('Checkout has local edits or untracked files. Review with athenaeumctl repo status; preserve them before syncing. Nothing was reset or stashed.')


def candidate_config(cfg, source, candidate):
    """Resolve tracked Compose files against the candidate, retaining /etc secrets.

    External overlays remain external. Relative Compose paths resolve inside the
    candidate checkout, so validation never needs to modify the live checkout.
    """
    files = []
    for name in cfg['compose_files']:
        path = Path(name)
        files.append(str(candidate / path.relative_to(source)) if path.is_relative_to(source) else name)
    return dict(cfg, compose_files=files)


def owned_temp(parent, owner):
    path = Path(tempfile.mkdtemp(prefix='.athenaeum-repo-', dir=parent))
    if os.geteuid() == 0:
        os.chown(path, owner.st_uid, owner.st_gid)
    return path


def repo_status(root=STACK):
    checkout(root, require_main=False)
    print('Checkout: ' + str(root))
    print('Commit: ' + git(root, 'rev-parse', 'HEAD'))
    print(git(root, 'status', '--short', '--branch'))
    print('Remote status reflects the last fetch. repo sync fetches GitHub before updating.')


def sync(cfg, root=STACK):
    with operation_lock(cfg):
        checkout(root)
        require_clean(root)
        before = git(root, 'rev-parse', 'HEAD')
        print('Fetching origin/main...', flush=True)
        git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
        after = git(root, 'rev-parse', 'refs/remotes/origin/main')
        if before == after:
            print('Repository already matches origin/main.')
            return
        if git(root, 'merge-base', before, after) != before:
            raise Failure('Local main is ahead of or diverged from origin/main. Preserve/reconcile that history manually; sync never resets it.')
        print(git(root, 'diff', '--stat', before, after), flush=True)
        # Validate a temporary checkout of the exact fetched commit before the
        # live fast-forward. Worktree bookkeeping and files stay owner-writable.
        temp = owned_temp(root.parent, root.stat())
        candidate = temp / 'candidate'
        try:
            git(root, 'worktree', 'add', '--detach', str(candidate), after)
            validate_compose(candidate_config(cfg, root, candidate))
            require_clean(root)
            if git(root, 'rev-parse', 'HEAD') != before:
                raise Failure('Checkout changed during validation; rerun after the other Git operation finishes.')
            git(root, 'merge', '--ff-only', after)
        finally:
            if candidate.exists():
                git(root, 'worktree', 'remove', '--force', str(candidate))
            shutil.rmtree(temp)
        print(f'Synced {before[:12]} -> {after[:12]}. Containers were not changed.')
        print('For Compose changes: athenaeumctl docker deploy')
        print('For new images too: athenaeumctl docker pull --deploy')
        print('For ops/ changes: athenaeumctl self update')
