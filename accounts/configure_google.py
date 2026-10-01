"""Install Google credentials interactively without printing or shell arguments."""
import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import stat
import tempfile


def configure(path, client_id, client_secret):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Use a real credential file without symlinked parents')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077:
        raise ValueError('Credential file must be a regular file with mode 0600')
    raw = path.read_text().strip()
    values = json.loads(raw) if raw.startswith('{') else {'session_secret': raw}
    if len(values['session_secret']) < 32:
        raise ValueError('Keep the original strong account session secret')
    if not client_id.endswith('.apps.googleusercontent.com') or not client_secret.strip():
        raise ValueError('Enter the Web application client ID and a nonempty client secret')
    values.update(google_client_id=client_id.strip(), google_client_secret=client_secret.strip())
    fd, temporary = tempfile.mkstemp(prefix='.accounts-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(values, file)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        if os.geteuid() == 0:
            os.chown(temporary, before.st_uid, before.st_gid)
        if path.read_text().strip() != raw:
            raise ValueError('Credential file changed; retry under the operation lock')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secret-file', default='/etc/athenaeum/accounts-session-secret')
    parser.add_argument('--lock-file', default='/var/lib/athenaeum/operation.lock')
    args = parser.parse_args()
    client_id = input('Google Web application client ID: ').strip()
    client_secret = getpass.getpass('Google client secret (hidden): ')
    lock = os.open(args.lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configure(args.secret_file, client_id, client_secret)
    finally:
        os.close(lock)
    print('Google credentials saved privately; the account session secret was preserved.')
    print('Recreate the accounts container to activate them. See docs/development/google-login.md.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        raise SystemExit('Credentials were not installed. Check the client values, private file permissions and operation lock; retry.')
