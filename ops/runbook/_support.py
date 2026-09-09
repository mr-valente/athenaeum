"""Small shared helpers for the operator scripts, not a provisioning framework.

Setup uses only Ubuntu's standard Python library. Runtime scripts reuse the
existing recovery package and its private virtualenv so OCI dependencies and
backup behavior have a single owner. No script runs just by importing this file.
"""
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ops'))
from athenaeum_ops.common import Failure, load_config, run  # noqa: E402

CONFIG = '/etc/athenaeum/recovery.json'
VENV = Path('/opt/athenaeum/operations/venv')


def say(message):
    print(message, flush=True)


def host(root=True):
    """Reject accidental execution on the Arch workstation before any mutation."""
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '26.04' or platform.machine() != 'aarch64':
        raise Failure('Run this step on the Ubuntu 26.04 ARM VM, not your workstation.')
    if root and os.geteuid() != 0:
        raise Failure('Run this VM step with sudo.')


def installed_config():
    host()
    if ROOT != Path('/opt/athenaeum/stack'):
        raise Failure('Run the scripts from the checkout at /opt/athenaeum/stack.')
    cfg = load_config(CONFIG)
    if cfg['mode'] != 'production':
        raise Failure('The VM runbook requires production recovery configuration.')
    return cfg


def recovery_python():
    """Restart under the installed SDK environment, keeping the same arguments.

    This happens only after argparse has handled --help and the host check has
    passed. The installer creates this environment; scripts never pip-install
    packages into the OS Python or into the application's container.
    """
    if not (VENV / 'bin/python').is_file():
        raise Failure('Install host services first; the recovery Python environment is missing.')
    if Path(sys.prefix) != VENV:
        os.execv(str(VENV / 'bin/python'), [str(VENV / 'bin/python'), *sys.argv])


def visible(args):
    """Show progress for non-secret package/service commands; stop on failure."""
    subprocess.run([str(a) for a in args], check=True)


def finish(main):
    # No tracebacks or child output that could accidentally disclose private
    # configuration. Individual scripts print their stage before each operation.
    os.umask(0o077)
    def interrupted(signum, frame):
        raise Failure("Interrupted; temporary files will be cleaned up.")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except (Failure, OSError, ValueError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as error:
        detail = str(error) if isinstance(error, Failure) else type(error).__name__ + '; inspect the last reported step'
        print('STOP: ' + detail, file=sys.stderr)
        raise SystemExit(1)
