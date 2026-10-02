import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .database import Database


logger = logging.getLogger(__name__)


def log_google_failure(phase, exc):
    # Exception messages, provider descriptions and request URLs may contain
    # credentials or codes. Log only the class and recognized protocol errors.
    known_errors = {'access_denied', 'invalid_client', 'invalid_grant', 'invalid_request',
                    'invalid_scope', 'unauthorized_client', 'server_error',
                    'temporarily_unavailable', 'mismatching_state', 'invalid_claim',
                    'missing_claim', 'expired_token', 'bad_signature'}
    error = getattr(exc, 'error', None)
    code = error if isinstance(error, str) and error in known_errors else 'unspecified'
    claim = getattr(exc, 'claim', None)
    known_claims = {'iss', 'aud', 'exp', 'iat', 'nonce', 'at_hash', 'sub', 'azp', 'email', 'email_verified'}
    claim_name = claim if isinstance(claim, str) and claim in known_claims else 'unspecified'
    logger.warning('Google sign-in failed: phase=%s exception=%s error=%s claim=%s',
                   phase, type(exc).__name__, code, claim_name)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def safe_return(value):
    # Relative, local paths only; never let OAuth turn into an open redirect.
    if (not value.startswith('/') or value.startswith('//') or '\\' in value
            or any(ord(c) < 32 for c in value) or '%' in value.split('?', 1)[0]):
        return '/account/'
    return value if urlsplit(value).netloc == '' else '/account/'


class IdentityRequest(BaseModel):
    token: str = Field(max_length=256)


class PerformanceRecord(BaseModel):
    source_id: str = Field(min_length=1, max_length=160)
    user_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    schema_version: int = Field(default=1, ge=1, le=1)
    activity: str = Field(min_length=1, max_length=80)
    occurred_at: str = Field(max_length=40)
    metrics: dict
    context: dict = Field(default_factory=dict)

    @field_validator('metrics', 'context')
    @classmethod
    def bounded_json(cls, value):
        if len(json.dumps(value, allow_nan=False)) > 32768:
            raise ValueError('Performance payload exceeds 32 KB')
        return value

    @field_validator('occurred_at')
    @classmethod
    def valid_timestamp(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError('Timestamp must include a timezone')
        return parsed.astimezone(timezone.utc).isoformat()


def create_app(settings: Settings):
    database = Database(settings.database)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        yield

    app = FastAPI(title='Athenaeum accounts', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.database = settings, database
    app.add_middleware(SessionMiddleware, secret_key=settings.secret,
                       session_cookie='athenaeum_oauth', max_age=600,
                       https_only=settings.secure, same_site='lax', path='/')
    templates = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))
    assets = Path(__file__).parent / 'static'
    app.mount('/account/static', StaticFiles(directory=assets), name='account_static')
    oauth = OAuth()
    if settings.google_enabled:
        oauth.register(name='google', client_id=settings.google_client_id,
                       client_secret=settings.google_client_secret,
                       server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                       client_kwargs={'scope': 'openid email profile', 'code_challenge_method': 'S256', 'timeout': 10})
    app.state.oauth = oauth

    def identity(token):
        if not token or len(token) > 256:
            return None
        with database.connect() as db:
            row = db.execute('SELECT u.* FROM account_sessions s JOIN users u ON u.id=s.user_id '
                             'WHERE s.token_hash=? AND s.expires_at>?', (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def current(request):
        return identity(request.cookies.get(settings.cookie, ''))

    def csrf(request):
        return request.session.setdefault('csrf', secrets.token_urlsafe(32))

    def check_csrf(request, value):
        expected = request.session.get('csrf', '')
        if not expected or not hmac.compare_digest(expected, value):
            raise HTTPException(403, 'The form expired. Reload the page and try again.')
        origin = request.headers.get('origin')
        if origin and origin != settings.origin:
            raise HTTPException(403, 'Cross-origin action rejected')

    def revoke(request, db):
        token = request.cookies.get(settings.cookie)
        if token:
            db.execute('DELETE FROM account_sessions WHERE token_hash=?', (digest(token),))

    def start_session(request, db, user_id, kind, destination):
        revoke(request, db)
        token = secrets.token_urlsafe(32)
        lifetime = 7 * 86400 if kind == 'guest' else 30 * 86400
        db.execute('DELETE FROM account_sessions WHERE expires_at<=?', (int(time.time()),))
        db.execute('INSERT INTO account_sessions VALUES (?,?,?)',
                   (digest(token), user_id, int(time.time()) + lifetime))
        request.session.clear()
        response = RedirectResponse(safe_return(destination), status_code=303)
        response.set_cookie(settings.cookie, token, max_age=lifetime, path='/',
                            secure=settings.secure, httponly=True, samesite='lax')
        return response

    def client(request: Request):
        app_id = request.headers.get('x-athenaeum-app', '')
        provided = request.headers.get('authorization', '').removeprefix('Bearer ')
        expected = settings.clients.get(app_id, '')
        if not expected or not hmac.compare_digest(provided, expected):
            raise HTTPException(401, 'Invalid application credential')
        return app_id

    @app.middleware('http')
    async def response_security(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; form-action 'self' https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get('/_health')
    def health():
        try:
            # Read-only health: do not create a replacement for a missing DB.
            with sqlite3.connect(settings.database.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                db.execute('SELECT id FROM users LIMIT 1')
            return {'status': 'ok'}
        except sqlite3.Error:
            return JSONResponse({'status': 'unavailable'}, status_code=503)

    @app.get('/account', include_in_schema=False)
    def account_redirect():
        return RedirectResponse('/account/', status_code=308)

    @app.get('/account/', response_class=HTMLResponse)
    def overview(request: Request, return_to: str = '/account/', page: int = 1,
                 profile_error: str = ''):
        user = current(request)
        page = max(1, min(page, 100000))
        with database.connect() as db:
            rows = db.execute('SELECT * FROM performance WHERE user_id=? '
                              'ORDER BY occurred_at DESC,app,source_id LIMIT 51 OFFSET ?',
                              (user['id'], (page - 1) * 50)).fetchall() if user else []
        records = [dict(r, metrics=json.loads(r['metrics_json']), context=json.loads(r['context_json'])) for r in rows[:50]]
        return templates.TemplateResponse(request=request, name='overview.html', context={
            'user': user, 'records': records, 'csrf': csrf(request),
            'google_enabled': settings.google_enabled, 'return_to': safe_return(return_to),
            'apps': sorted(settings.clients), 'page': page, 'has_next': len(rows) > 50,
            'error': request.query_params.get('error'),
            'profile_error': profile_error,
        })

    @app.get('/account/status')
    def status(request: Request):
        # The static site only needs a label; never send it account details.
        return {'signed_in': current(request) is not None}

    @app.post('/account/profile')
    def profile(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
        check_csrf(request, csrf_token)
        user = current(request)
        if not user or user['kind'] != 'google':
            raise HTTPException(401, 'Sign in with Google to set your name')
        normalized = ' '.join(name.split())
        if (not normalized or len(normalized) > 60
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            response = overview(request, profile_error='Use a name between 1 and 60 characters.')
            response.status_code = 400
            return response
        with database.connect() as db:
            db.execute('UPDATE users SET name=? WHERE id=?', (normalized, user['id']))
        return RedirectResponse('/account/', status_code=303)

    @app.post('/auth/guest')
    def guest(request: Request, csrf_token: str = Form(...), return_to: str = Form('/account/')):
        check_csrf(request, csrf_token)
        existing = current(request)
        if existing:
            return RedirectResponse(safe_return(return_to), status_code=303)
        with database.connect() as db:
            user_id = str(uuid.uuid4())
            db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user_id, 'guest', 'Guest', None, now()))
            return start_session(request, db, user_id, 'guest', return_to)

    @app.post('/auth/google')
    async def google_login(request: Request, csrf_token: str = Form(...), return_to: str = Form('/account/')):
        check_csrf(request, csrf_token)
        if not settings.google_enabled:
            raise HTTPException(503, 'Google sign-in is not configured yet. Guest access remains available.')
        request.session['return_to'] = safe_return(return_to)
        try:
            return await oauth.google.authorize_redirect(request, settings.origin + '/auth/google/callback',
                                                          prompt='select_account')
        except Exception as exc:
            log_google_failure('authorization', exc)
            return RedirectResponse('/account/?error=google-unavailable', status_code=303)

    @app.get('/auth/google/callback')
    async def google_callback(request: Request):
        if not settings.google_enabled:
            raise HTTPException(503, 'Google sign-in is not configured')
        phase = 'callback'
        try:
            token = await oauth.google.authorize_access_token(request)
            phase = 'identity'
            userinfo = token['userinfo']  # Authlib validates signature, issuer, audience, expiry and nonce.
            subject = userinfo['sub']
            if (not isinstance(subject, str) or not subject or len(subject) > 255
                    or userinfo.get('email_verified') is not True
                    or not isinstance(userinfo.get('email'), str) or not userinfo['email']):
                raise ValueError('Missing verified identity')
        except Exception as exc:
            log_google_failure(phase, exc)
            request.session.clear()
            return RedirectResponse('/account/?error=google-login-failed', status_code=303)
        destination = request.session.get('return_to', '/account/')
        with database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT user_id FROM identities WHERE provider=? AND subject=?', ('google', subject)).fetchone()
            user_id = row['user_id'] if row else str(uuid.uuid4())
            name = str(userinfo.get('name') or 'Google account')[:120]
            email = str(userinfo['email'])[:320]
            if row:
                # Google supplies the initial name; later sign-ins preserve the
                # user's chosen display name.
                db.execute('UPDATE users SET email=? WHERE id=?', (email, user_id))
            else:
                db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user_id, 'google', name, email, now()))
                db.execute('INSERT INTO identities VALUES (?,?,?)', ('google', subject, user_id))
            db.execute('INSERT INTO account_audit(user_id,action,created_at) VALUES (?,?,?)', (user_id, 'google_login', now()))
            # Existing guest activity is linked explicitly inside its owning app.
            return start_session(request, db, user_id, 'google', destination)

    @app.post('/auth/logout')
    def logout(request: Request, csrf_token: str = Form(...)):
        check_csrf(request, csrf_token)
        with database.connect() as db:
            revoke(request, db)
        request.session.clear()
        response = RedirectResponse('/account/', status_code=303)
        response.delete_cookie(settings.cookie, path='/', secure=settings.secure, httponly=True, samesite='lax')
        return response

    @app.post('/auth/logout-all')
    def logout_all(request: Request, csrf_token: str = Form(...)):
        check_csrf(request, csrf_token)
        user = current(request)
        if user:
            with database.connect() as db:
                db.execute('DELETE FROM account_sessions WHERE user_id=?', (user['id'],))
        request.session.clear()
        response = RedirectResponse('/account/', status_code=303)
        response.delete_cookie(settings.cookie, path='/', secure=settings.secure, httponly=True, samesite='lax')
        return response

    @app.get('/account/export')
    def export(request: Request):
        user = current(request)
        if not user:
            raise HTTPException(401, 'Sign in to export your history')
        with database.connect() as db:
            rows = db.execute('SELECT * FROM performance WHERE user_id=? ORDER BY occurred_at', (user['id'],)).fetchall()
        records = [dict(r, metrics=json.loads(r['metrics_json']), context=json.loads(r['context_json'])) for r in rows]
        for record in records:
            del record['metrics_json'], record['context_json']
        return JSONResponse({'account': user, 'performance': records}, headers={
            'Content-Disposition': 'attachment; filename="athenaeum-history.json"'})

    @app.post('/internal/identity')
    def resolve(payload: IdentityRequest, app_id=Depends(client)):
        return {'user': identity(payload.token)}

    @app.post('/internal/performance')
    def record(payload: PerformanceRecord, app_id=Depends(client)):
        with database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT kind FROM users WHERE id=?', (payload.user_id,)).fetchone()
            if not user or user['kind'] != 'google':
                raise HTTPException(422, 'A persistent account is required')
            old = db.execute('SELECT user_id,revision FROM performance WHERE app=? AND source_id=?',
                             (app_id, payload.source_id)).fetchone()
            if old and old['user_id'] != payload.user_id:
                raise HTTPException(409, 'Performance ownership cannot be reassigned')
            if old and old['revision'] >= payload.revision:
                return {'status': 'already-recorded'}
            db.execute('INSERT INTO performance VALUES (?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(app,source_id) DO UPDATE SET revision=excluded.revision,'
                       'metrics_json=excluded.metrics_json,context_json=excluded.context_json,'
                       'occurred_at=excluded.occurred_at,activity=excluded.activity',
                       (app_id, payload.source_id, payload.user_id, payload.revision, payload.activity,
                        payload.occurred_at, json.dumps(payload.metrics, allow_nan=False),
                        json.dumps(payload.context, allow_nan=False), payload.schema_version))
            if not old:
                db.execute('INSERT INTO account_audit(user_id,action,app,source_id,created_at) VALUES (?,?,?,?,?)',
                           (payload.user_id, 'performance_created', app_id, payload.source_id, now()))
        return {'status': 'recorded'}

    return app
