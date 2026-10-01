import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def app_key(secret: str, app: str) -> str:
    return hmac.new(secret.encode(), f'athenaeum-account-api-v1:{app}'.encode(), hashlib.sha256).hexdigest()


@dataclass
class Settings:
    database: Path
    secret: str
    origin: str = 'http://localhost:8000'
    production: bool = False
    google_client_id: str = ''
    google_client_secret: str = ''
    clients: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.origin = self.origin.rstrip('/')
        url = urlsplit(self.origin)
        if (not url.netloc or url.path or url.query or url.fragment or url.username
                or url.scheme not in ('http', 'https')
                or (self.production and url.scheme != 'https')):
            raise ValueError('ACCOUNT_ORIGIN must be an origin, using HTTPS in production')
        if len(self.secret) < 32 or not self.database.is_absolute():
            raise ValueError('Accounts require an absolute DB_PATH and a persistent secret of at least 32 characters')

    @property
    def cookie(self):
        return '__Host-athenaeum_account' if self.production else 'athenaeum_account'

    @property
    def secure(self):
        return self.origin.startswith('https://')

    @property
    def google_enabled(self):
        return bool(self.google_client_id and self.google_client_secret)


def from_environment():
    raw = Path(os.environ['SESSION_SECRET_FILE']).read_text().strip()
    credentials = json.loads(raw) if raw.startswith('{') else {'session_secret': raw}
    files = json.loads(os.environ.get('ACCOUNTS_APP_SECRETS', '{}'))
    return Settings(
        database=Path(os.environ.get('DB_PATH', '/data/app.db')),
        secret=credentials['session_secret'],
        origin=os.environ.get('ACCOUNT_ORIGIN', 'https://valentemath.com'),
        production=os.environ.get('APP_ENV') == 'production',
        google_client_id=credentials.get('google_client_id', ''),
        google_client_secret=credentials.get('google_client_secret', ''),
        clients={app: app_key(Path(path).read_text().strip(), app) for app, path in files.items()},
    )
