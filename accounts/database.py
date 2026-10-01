import contextlib
import sqlite3


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('guest','google')),
 name TEXT NOT NULL, email TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
 provider TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 PRIMARY KEY(provider,subject)
);
CREATE TABLE IF NOT EXISTS account_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS performance (
 app TEXT NOT NULL, source_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 revision INTEGER NOT NULL, activity TEXT NOT NULL, occurred_at TEXT NOT NULL,
 metrics_json TEXT NOT NULL, context_json TEXT NOT NULL, schema_version INTEGER NOT NULL,
 PRIMARY KEY(app,source_id)
);
CREATE INDEX IF NOT EXISTS performance_user ON performance(user_id,occurred_at);
CREATE TABLE IF NOT EXISTS account_audit (
 id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 app TEXT, source_id TEXT, created_at TEXT NOT NULL
);
'''


class Database:
    def __init__(self, path):
        self.path = path

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        if not self.path.parent.is_dir():
            raise RuntimeError('Mount the accounts data directory before starting the service')
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError('Unsupported accounts database version; select a compatible image')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            db.execute('PRAGMA user_version=1')
