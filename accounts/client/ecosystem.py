"""Pinned Athenaeum app adapter v1. Keep copies identical in hosted consumers.

Uses separate additive tables; classroom models remain app-owned. No network
calls run inside a classroom transaction. Guest mode also works standalone.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from urllib import error, request as http

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Column, Integer, String, Text, UniqueConstraint, event, or_
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import SESSION_SECRET
from app.database import Base, SessionLocal, get_db

SERVICE_URL = os.environ.get('ACCOUNT_SERVICE_URL', '').rstrip('/')
APP_ID = os.environ.get('ATHENAEUM_APP_ID', '')
ENABLED = bool(SERVICE_URL)
if ENABLED and (not SERVICE_URL.startswith('http://') or not APP_ID):
    raise RuntimeError('Configure the private ACCOUNT_SERVICE_URL and ATHENAEUM_APP_ID together')
API_KEY = hmac.new(SESSION_SECRET.encode(), f'athenaeum-account-api-v1:{APP_ID}'.encode(), hashlib.sha256).hexdigest()
log = logging.getLogger('athenaeum.accounts')
router = APIRouter()
_snapshot = None


class AccountLink(Base):
    __tablename__ = 'ecosystem_links'
    id = Column(String, primary_key=True)  # role:local UUID
    role = Column(String, nullable=False)
    local_id = Column(String, nullable=False)
    account_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('role', 'local_id'),)


class PerformanceOutbox(Base):
    __tablename__ = 'ecosystem_outbox'
    id = Column(String, primary_key=True)  # stable app-specific source record
    revision = Column(Integer, nullable=False, default=1)
    delivered = Column(Integer, nullable=False, default=0)
    payload = Column(Text, nullable=False)
    last_error = Column(String, nullable=True)


def account(request):
    return getattr(request.state, 'ecosystem_account', None)


def api(path, payload):
    data = json.dumps(payload, allow_nan=False).encode()
    req = http.Request(SERVICE_URL + path, data=data, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY,
        'X-Athenaeum-App': APP_ID,
    })
    try:
        # Private application credentials must never travel through an ambient proxy.
        with http.build_opener(http.ProxyHandler({})).open(req, timeout=2) as response:
            return json.loads(response.read(131072))
    except error.HTTPError as exc:
        raise RuntimeError('account-service-http-' + str(exc.code)) from None
    except (OSError, ValueError):
        raise RuntimeError('account-service-unavailable') from None


class AccountMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.ecosystem_enabled = ENABLED
        request.state.ecosystem_account = None
        request.state.ecosystem_unavailable = False
        token = request.cookies.get('__Host-athenaeum_account') or request.cookies.get('athenaeum_account')
        if ENABLED and token:
            try:
                resolved = await asyncio.to_thread(api, '/internal/identity', {'token': token})
                request.state.ecosystem_account = resolved['user']
            except (RuntimeError, KeyError):
                request.state.ecosystem_unavailable = True
        response = await call_next(request)
        if ENABLED:
            response.headers['Cache-Control'] = 'no-store'
        return response


def link_for(db, role, local_id):
    return db.get(AccountLink, role + ':' + local_id)


def permitted(request, db, role, obj):
    if obj is None:
        return False
    link = link_for(db, role, obj.id)
    user = account(request)
    if not link:
        return True  # caller must first prove the original browser's rejoin token
    if user:
        return user['id'] == link.account_id
    # A service outage may retain an already established seat for this browser.
    # The raw account cookie must still match the binding made when last validated.
    token = request.cookies.get('__Host-athenaeum_account') or request.cookies.get('athenaeum_account', '')
    saved = request.session.get('ecosystem_binding', {})
    return bool(getattr(request.state, 'ecosystem_unavailable', False) and token
                and saved.get('id') == link.account_id
                and hmac.compare_digest(saved.get('token_hash', ''), hashlib.sha256(token.encode()).hexdigest()))


def bind(request, db, role, obj, *, explicit=False):
    user = account(request)
    if not user:
        return
    existing = link_for(db, role, obj.id)
    if existing:
        if existing.account_id != user['id']:
            if not explicit or existing.kind != 'guest':
                raise HTTPException(409, 'This activity belongs to another account')
            existing.account_id, existing.kind = user['id'], user['kind']
    else:
        db.add(AccountLink(id=role + ':' + obj.id, role=role, local_id=obj.id,
                           account_id=user['id'], kind=user['kind']))
    token = request.cookies.get('__Host-athenaeum_account') or request.cookies.get('athenaeum_account', '')
    request.session['ecosystem_binding'] = {'id': user['id'], 'token_hash': hashlib.sha256(token.encode()).hexdigest()}


def owned(request, db, role, model, session_id=None):
    user = account(request)
    if not user:
        return None
    query = db.query(model).join(AccountLink, AccountLink.local_id == model.id).filter(
        AccountLink.role == role, AccountLink.account_id == user['id'])
    if session_id:
        query = query.filter(model.session_id == session_id)
    if role == 'teacher':
        profiles = query.all()
        if profiles:
            profiles[0].ecosystem_teacher_ids = [profile.id for profile in profiles]
            return profiles[0]
        return None
    return query.first()


def remember_binding(request):
    user = account(request)
    if user:
        token = request.cookies.get('__Host-athenaeum_account') or request.cookies.get('athenaeum_account', '')
        request.session['ecosystem_binding'] = {'id': user['id'], 'token_hash': hashlib.sha256(token.encode()).hexdigest()}


def enqueue(db, session_ids=None):
    if not ENABLED or not _snapshot:
        return
    for payload in _snapshot(db, session_ids):
        source_id = payload['source_id']
        row = db.get(PerformanceOutbox, source_id)
        serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
        if row is None:
            db.add(PerformanceOutbox(id=source_id, revision=1, delivered=0, payload=serialized))
        elif row.payload != serialized:
            row.revision += 1
            row.payload, row.last_error = serialized, None


def install(snapshot):
    global _snapshot
    _snapshot = snapshot

    @event.listens_for(SessionLocal, 'before_flush')
    def collect_changes(db, context, instances):
        touched = db.info.setdefault('ecosystem_sessions', set())
        from app.models import Session, Player
        for obj in list(db.new) + list(db.dirty):
            if isinstance(obj, (AccountLink, PerformanceOutbox)):
                continue
            if isinstance(obj, Session):
                touched.add(obj.id)
            elif isinstance(obj, Player):
                touched.add(obj.session_id)
            elif getattr(obj, 'session_id', None):
                touched.add(obj.session_id)
            elif getattr(obj, 'round_id', None):
                from app.experiments.flip_flop.models import FlipFlopRound
                rnd = db.get(FlipFlopRound, obj.round_id)
                if rnd:
                    touched.add(rnd.session_id)

    @event.listens_for(SessionLocal, 'after_flush_postexec')
    def save_pending(db, context):
        touched = db.info.pop('ecosystem_sessions', set())
        if touched:
            enqueue(db, touched)


def flush_outbox(limit=50):
    if not ENABLED:
        return 0
    with SessionLocal() as db:
        rows = db.query(PerformanceOutbox).filter(
            PerformanceOutbox.delivered < PerformanceOutbox.revision,
            or_(PerformanceOutbox.last_error.is_(None),
                PerformanceOutbox.last_error.notin_(['account-service-http-409', 'account-service-http-422']))
        ).limit(limit).all()
        batch = [(r.id, r.revision, json.loads(r.payload)) for r in rows]
    sent = 0
    for source_id, revision, payload in batch:
        try:
            api('/internal/performance', dict(payload, revision=revision, schema_version=1))
        except RuntimeError as exc:
            with SessionLocal() as db:
                row = db.get(PerformanceOutbox, source_id)
                if row:
                    row.last_error = str(exc)
                    db.commit()
            log.warning('Performance synchronization paused: %s', exc)
            if str(exc) in ('account-service-http-409', 'account-service-http-422'):
                # Retain rejected records for review without starving other users.
                # A corrected snapshot clears last_error and retries a new revision.
                continue
            break
        with SessionLocal() as db:
            row = db.get(PerformanceOutbox, source_id)
            if row:
                row.delivered = max(row.delivered, revision)
                row.last_error = None
                db.commit()
        sent += 1
    return sent


async def sync_worker(stop):
    # Reconcile finalized records at startup as well as transactionally on changes.
    if ENABLED:
        with SessionLocal() as db:
            enqueue(db)
            db.commit()
    while not stop.is_set():
        await asyncio.to_thread(flush_outbox)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


def browser_proofs(request, db):
    """Only signed app-cookie tokens prove ownership of pre-account records."""
    from app.models import Teacher, Player
    teacher = db.get(Teacher, request.session.get('teacher_id', ''))
    if teacher and request.session.get('teacher_rejoin_token') == teacher.rejoin_token:
        yield 'teacher', teacher
    remembered = request.session.get('players', {})
    if isinstance(remembered, dict):
        for entry in remembered.values():
            if not isinstance(entry, dict):
                continue
            player = db.get(Player, entry.get('id', ''))
            if player and entry.get('token') == player.rejoin_token:
                yield 'player', player
    player = db.get(Player, request.session.get('player_id', ''))
    if player and request.session.get('rejoin_token') == player.rejoin_token:
        yield 'player', player


@router.get('/account', response_class=HTMLResponse)
def app_account(request: Request, db=Depends(get_db)):
    from app.templating import templates
    if not ENABLED:
        raise HTTPException(404)
    user = account(request)
    request.session.setdefault('account_csrf', secrets.token_urlsafe(32))
    records = []
    for role, obj in browser_proofs(request, db):
        link = link_for(db, role, obj.id)
        if link is None or link.kind == 'guest':
            records.append({'role': role, 'id': obj.id, 'name': obj.name,
                            'session_id': getattr(obj, 'session_id', None)})
    pending = db.query(PerformanceOutbox).filter(PerformanceOutbox.delivered < PerformanceOutbox.revision).count()
    return templates.TemplateResponse(request=request, name='account.html', context={
        'account_user': user, 'claimable': records, 'csrf': request.session['account_csrf'],
        'pending': pending, 'error': request.query_params.get('error'),
    })


@router.post('/account/link')
def save_activity(request: Request, role: str = Form(...), local_id: str = Form(...),
                  csrf_token: str = Form(...), db=Depends(get_db)):
    if not ENABLED:
        raise HTTPException(404)
    expected = request.session.get('account_csrf', '')
    if not expected or not hmac.compare_digest(expected, csrf_token):
        raise HTTPException(403, 'Reload the account page and try again')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        # base_url includes the app root_path; compare just scheme and host.
        from urllib.parse import urlsplit
        base = urlsplit(str(request.base_url))
        if origin != f'{base.scheme}://{base.netloc}':
            raise HTTPException(403, 'Cross-origin action rejected')
    user = account(request)
    if not user or user['kind'] != 'google':
        raise HTTPException(401, 'Sign in with Google before saving activity')
    for proof_role, obj in browser_proofs(request, db):
        if proof_role == role and obj.id == local_id:
            bind(request, db, role, obj, explicit=True)
            db.flush()
            enqueue(db)
            db.commit()
            return RedirectResponse(request.url_for('app_account'), status_code=303)
    raise HTTPException(403, 'This browser cannot prove ownership of that activity')
