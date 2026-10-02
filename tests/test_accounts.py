import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from accounts.config import Settings, app_key
from accounts.configure_google import configure
from accounts.main import create_app, digest, safe_return


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name) / 'app.db', 'test-secret-' * 4,
                                 origin='https://valentemath.com', production=True,
                                 clients={'bernoulli': app_key('bernoulli-secret-' * 4, 'bernoulli'),
                                          'quacktuaries': app_key('quack-secret-' * 4, 'quacktuaries')})
        self.app = create_app(self.settings)
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.origin))

    def csrf(self, client=None):
        html = (client or self.client).get('/account/').text
        return re.search(r'name="csrf_token" value="([^"]+)"', html)[1]

    def google_user(self, client=None, user_id='persistent-user'):
        client = client or self.client
        token = 'session-' + user_id
        with self.app.state.database.connect() as db:
            db.execute('INSERT OR IGNORE INTO users VALUES (?,?,?,?,?)',
                       (user_id, 'google', '<Account>', 'student@example.test', '2026-01-01T00:00:00+00:00'))
            db.execute('INSERT OR REPLACE INTO account_sessions VALUES (?,?,?)',
                       (digest(token), user_id, int(time.time()) + 10000))
        client.cookies.set(self.settings.cookie, token, domain='valentemath.com', path='/')
        return user_id

    def api(self, endpoint, payload, app='bernoulli'):
        return self.client.post(endpoint, json=payload, headers={
            'X-Athenaeum-App': app, 'Authorization': 'Bearer ' + self.settings.clients[app]})

    def record(self, user_id, **updates):
        return dict(source_id='vote-123', user_id=user_id, revision=1, activity='flip_flop',
                    occurred_at='2026-10-01T12:00:00+00:00', metrics={'correct': True}, context={}, **updates)

    def test_guest_cookie_csrf_and_revocation(self):
        self.assertEqual(self.client.post('/auth/guest', data={'csrf_token': 'wrong'}).status_code, 403)
        token = self.csrf()
        self.assertEqual(self.client.post('/auth/guest', data={'csrf_token': token},
                                         headers={'Origin': 'https://other.test'}).status_code, 403)
        response = self.client.post('/auth/guest', data={'csrf_token': token, 'return_to': '//attacker.test'}, follow_redirects=False)
        self.assertEqual(response.headers['location'], '/account/')
        cookie = response.headers['set-cookie']
        for value in ('HttpOnly', 'Secure', 'SameSite=lax', 'Path=/'):
            self.assertIn(value, cookie)
        self.assertNotIn('Domain=', cookie)
        raw = self.client.cookies.get(self.settings.cookie)
        self.assertEqual(self.api('/internal/identity', {'token': raw}).json()['user']['kind'], 'guest')
        with self.app.state.database.connect() as db:
            stored = db.execute('SELECT token_hash FROM account_sessions').fetchone()[0]
        self.assertNotEqual(stored, raw)
        self.client.post('/auth/logout', data={'csrf_token': self.csrf()})
        self.assertIsNone(self.api('/internal/identity', {'token': raw}).json()['user'])

    def test_internal_auth_and_deduplication_ownership_and_revision(self):
        user_id = self.google_user()
        payload = self.record(user_id)
        self.assertEqual(self.client.post('/internal/performance', json=payload).status_code, 401)
        self.assertEqual(self.api('/internal/performance', payload).json()['status'], 'recorded')
        self.assertEqual(self.api('/internal/performance', payload).json()['status'], 'already-recorded')
        updated = dict(payload, revision=2, metrics={'correct': False})
        self.assertEqual(self.api('/internal/performance', updated).status_code, 200)
        self.api('/internal/performance', payload)  # stale delivery cannot roll back newer data
        other = self.google_user(user_id='other-user')
        self.assertEqual(self.api('/internal/performance', dict(payload, user_id=other, revision=3)).status_code, 409)
        self.assertEqual(self.api('/internal/performance', payload, app='quacktuaries').status_code, 200)
        with self.app.state.database.connect() as db:
            rows = db.execute('SELECT app,revision,metrics_json FROM performance ORDER BY app').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['revision'], 2)
            self.assertFalse(json.loads(rows[0]['metrics_json'])['correct'])

    def test_history_privacy_export_xss_and_expired_sessions(self):
        first = self.google_user()
        self.api('/internal/performance', self.record(first))
        self.assertIn('&lt;Account&gt;', self.client.get('/account/').text)
        self.assertEqual(len(self.client.get('/account/export').json()['performance']), 1)
        self.google_user(user_id='second-user')
        self.assertEqual(self.client.get('/account/export').json()['performance'], [])
        with self.app.state.database.connect() as db:
            db.execute('UPDATE account_sessions SET expires_at=0')
        self.assertEqual(self.client.get('/account/export').status_code, 401)

    def test_disabled_google_guest_and_health_do_not_need_google(self):
        self.assertEqual(self.client.post('/auth/google', data={'csrf_token': self.csrf()}).status_code, 503)
        self.assertEqual(self.client.get('/_health').status_code, 200)
        self.settings.database.unlink()
        self.assertEqual(self.client.get('/_health').status_code, 503)
        self.assertFalse(self.settings.database.exists())

    def test_logout_all_revokes_other_devices(self):
        user = self.google_user()
        with self.app.state.database.connect() as db:
            db.execute('INSERT INTO account_sessions VALUES (?,?,?)', (digest('other-device'), user, int(time.time()) + 1000))
        self.client.post('/auth/logout-all', data={'csrf_token': self.csrf()})
        self.assertIsNone(self.api('/internal/identity', {'token': 'other-device'}).json()['user'])

    def test_display_name_and_minimal_status_are_private_and_csrf_protected(self):
        response = self.client.get('/account/status')
        self.assertEqual(response.json(), {'signed_in': False})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.post('/account/profile', data={'name': 'Mr. Valente', 'csrf_token': self.csrf()}).status_code, 401)
        self.google_user()
        self.assertEqual(self.client.get('/account/status').json(), {'signed_in': True})
        csrf = self.csrf()
        self.assertEqual(self.client.post('/account/profile', data={'name': 'Mr. Valente', 'csrf_token': 'wrong'}).status_code, 403)
        self.assertEqual(self.client.post('/account/profile', data={'name': 'Mr. Valente', 'csrf_token': csrf}, headers={'Origin': 'https://other.test'}).status_code, 403)
        for name in ('   ', 'x' * 61, 'new\nname'):
            self.assertEqual(self.client.post('/account/profile', data={'name': name, 'csrf_token': csrf}).status_code, 400)
        response = self.client.post('/account/profile', data={'name': '  Mr.   Valente  ', 'csrf_token': csrf}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get('/account/export').json()['account']['name'], 'Mr. Valente')
        html = self.client.get('/account/').text
        self.assertNotIn('Sign out on every device', html)
        self.assertIn('href="/bernoulli/account">Bernoulli</a>', html)
        self.assertNotIn('href="/bernoulli/"', html)

    def test_provider_failure_does_not_create_an_account(self):
        settings = Settings(Path(self.temp.name) / 'oauth.db', 'secret' * 8,
                            origin='https://valentemath.com', production=True,
                            google_client_id='test.apps.googleusercontent.com', google_client_secret='private')
        app = create_app(settings)
        with TestClient(app, base_url=settings.origin) as client:
            with self.assertLogs('accounts.main', level='WARNING') as logs:
                response = client.get('/auth/google/callback?state=forged&code=forged', follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn('google-login-failed', response.headers['location'])
            self.assertIn('phase=callback exception=MismatchingStateError error=mismatching_state', logs.output[0])
            with app.state.database.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_google_diagnostics_exclude_provider_details_and_credentials(self):
        from authlib.integrations.base_client.errors import OAuthError
        settings = Settings(Path(self.temp.name) / 'diagnostics.db', 'secret' * 8,
                            origin=self.settings.origin, production=True,
                            google_client_id='test.apps.googleusercontent.com', google_client_secret='private')
        app = create_app(settings)
        for error, expected in (('invalid_client', 'invalid_client'), ('secret-provider-value', 'unspecified')):
            failure = OAuthError(error=error, description='private-secret code=sensitive-code token=private-token')
            with TestClient(app, base_url=settings.origin) as client:
                for method, path, phase in (('authorize_redirect', '/auth/google', 'authorization'),
                                             ('authorize_access_token', '/auth/google/callback', 'callback')):
                    with patch.object(app.state.oauth.google, method, AsyncMock(side_effect=failure)):
                        with self.assertLogs('accounts.main', level='WARNING') as logs:
                            if phase == 'authorization':
                                response = client.post(path, data={'csrf_token': self.csrf(client)}, follow_redirects=False)
                            else:
                                response = client.get(path, follow_redirects=False)
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(logs.output, [f'WARNING:accounts.main:Google sign-in failed: phase={phase} exception=OAuthError error={expected} claim=unspecified'])
                    with app.state.database.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)
        from accounts.main import log_google_failure
        from joserfc.errors import InvalidClaimError
        for claim, expected in (('iss', 'iss'), ('private-token', 'unspecified')):
            with self.assertLogs('accounts.main', level='WARNING') as logs:
                log_google_failure('callback', InvalidClaimError(claim))
            self.assertEqual(logs.output, [f'WARNING:accounts.main:Google sign-in failed: phase=callback exception=InvalidClaimError error=invalid_claim claim={expected}'])

    def test_provider_success_uses_subject_and_never_merges_guest(self):
        settings = Settings(Path(self.temp.name) / 'oauth.db', 'secret' * 8,
                            origin='https://valentemath.com', production=True,
                            google_client_id='test.apps.googleusercontent.com', google_client_secret='private')
        app = create_app(settings)
        with TestClient(app, base_url=settings.origin) as client:
            client.post('/auth/guest', data={'csrf_token': self.csrf(client)})
            with app.state.database.connect() as db:
                guest_id = db.execute('SELECT id FROM users').fetchone()[0]
            mock = AsyncMock(return_value={'userinfo': {'sub': 'stable-sub', 'email': 'old@example.test', 'email_verified': True}})
            with patch.object(app.state.oauth.google, 'authorize_access_token', mock):
                client.get('/auth/google/callback')
                client.post('/account/profile', data={'name': 'Mr. Valente', 'csrf_token': self.csrf(client)})
                mock.return_value['userinfo']['email'] = 'changed@example.test'
                client.get('/auth/google/callback')
            with app.state.database.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 2)
                persistent = db.execute("SELECT * FROM users WHERE kind='google'").fetchone()
                self.assertNotEqual(persistent['id'], guest_id)
                self.assertEqual(persistent['email'], 'changed@example.test')
                self.assertEqual(persistent['name'], 'Mr. Valente')

    def test_configure_preserves_secret_permissions_and_google_values(self):
        path = Path(self.temp.name) / 'credentials'
        original = 'original-secret-' * 4
        path.write_text(original)
        path.chmod(0o600)
        configure(path, 'id.apps.googleusercontent.com', 'hidden-google-secret')
        values = json.loads(path.read_text())
        self.assertEqual(values['session_secret'], original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        configure(path, 'new.apps.googleusercontent.com', 'replacement')
        self.assertEqual(json.loads(path.read_text())['session_secret'], original)

    def test_online_snapshot_preserves_real_account_schema_and_history(self):
        from accounts.database import Database
        from accounts.main import digest
        from accounts.config import Settings
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
        from athenaeum_ops.archive import snapshot_database
        from athenaeum_ops.common import APPS
        user = self.google_user()
        self.api('/internal/performance', self.record(user))
        restored = Path(self.temp.name) / 'restored.db'
        metadata = snapshot_database(self.settings.database, restored, APPS['accounts']['tables'])
        self.assertEqual(metadata['row_counts']['users'], 1)
        self.assertEqual(metadata['row_counts']['performance'], 1)
        replacement = create_app(Settings(restored, self.settings.secret, origin=self.settings.origin,
                                         production=True, clients=self.settings.clients))
        with TestClient(replacement, base_url=self.settings.origin) as client:
            client.cookies.set(self.settings.cookie, 'session-' + user, domain='valentemath.com', path='/')
            self.assertEqual(len(client.get('/account/export').json()['performance']), 1)

    def test_redirect_restrictions(self):
        for unsafe in ('//evil.test', 'https://evil.test', '/\\evil.test', '/%2f%2fevil.test', '/\r\nevil'):
            self.assertEqual(safe_return(unsafe), '/account/')
        self.assertEqual(safe_return('/bernoulli/join?code=ABC123'), '/bernoulli/join?code=ABC123')

    def test_real_oidc_validation_signature_audience_issuer_nonce_expiry_and_pkce(self):
        import httpx
        from joserfc import jwt
        from joserfc.jwk import RSAKey
        from urllib.parse import parse_qs, urlsplit
        trusted = RSAKey.generate_key(2048, parameters={'kid': 'fixture'})
        untrusted = RSAKey.generate_key(2048, parameters={'kid': 'fixture'})
        for case in ('valid', 'signature', 'audience', 'issuer', 'nonce', 'expiry', 'state'):
            with self.subTest(case=case):
                settings = Settings(Path(self.temp.name) / (case + '.db'), 'secret' * 8,
                    origin='https://valentemath.com', production=True,
                    google_client_id='test.apps.googleusercontent.com', google_client_secret='private')
                app = create_app(settings)
                google = app.state.oauth.google
                metadata = {
                    'issuer': 'https://accounts.google.com',
                    'authorization_endpoint': 'https://accounts.google.com/o/oauth2/v2/auth',
                    'token_endpoint': 'https://oauth2.googleapis.com/token',
                    'jwks_uri': 'https://www.googleapis.com/oauth2/v3/certs',
                    'id_token_signing_alg_values_supported': ['RS256'],
                }
                params = {}
                exchanged = []
                def provider(request):
                    if str(request.url) == 'https://accounts.google.com/.well-known/openid-configuration':
                        return httpx.Response(200, json=metadata)
                    if str(request.url) == metadata['jwks_uri']:
                        return httpx.Response(200, json={'keys': [trusted.as_dict(private=False)]})
                    self.assertEqual(str(request.url), 'https://oauth2.googleapis.com/token')
                    exchanged.append(parse_qs(request.content.decode()))
                    claims = {'iss': 'https://accounts.google.com', 'aud': settings.google_client_id,
                              'sub': 'subject-123', 'nonce': params['nonce'][0],
                              'iat': int(time.time()), 'exp': int(time.time()) + 3600,
                              'email': 'student@example.test', 'email_verified': True}
                    for field, value in {'audience': ('aud', 'wrong-client'), 'issuer': ('iss', 'https://evil.test'),
                                         'nonce': ('nonce', 'wrong-nonce'), 'expiry': ('exp', int(time.time()) - 3600)}.items():
                        if case == field:
                            claims[value[0]] = value[1]
                    signed = jwt.encode({'alg': 'RS256', 'kid': 'fixture'}, claims,
                                        untrusted if case == 'signature' else trusted)
                    return httpx.Response(200, json={'access_token': 'transient-token', 'token_type': 'Bearer',
                                                    'id_token': signed})
                google.client_kwargs['transport'] = httpx.MockTransport(provider)
                with TestClient(app, base_url=settings.origin) as client:
                    response = client.post('/auth/google', data={'csrf_token': self.csrf(client),
                                           'return_to': '/bernoulli/join'}, follow_redirects=False)
                    self.assertEqual(response.status_code, 302)
                    params.update(parse_qs(urlsplit(response.headers['location']).query))
                    self.assertEqual(params['code_challenge_method'], ['S256'])
                    state = 'forged-state' if case == 'state' else params['state'][0]
                    response = client.get('/auth/google/callback', params={'state': state, 'code': 'fixture-code'},
                                          follow_redirects=False)
                    if case == 'valid':
                        self.assertEqual(response.headers['location'], '/bernoulli/join')
                        self.assertTrue(exchanged[0]['code_verifier'][0])
                    else:
                        self.assertIn('google-login-failed', response.headers['location'])
                    with app.state.database.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 1 if case == 'valid' else 0)


if __name__ == '__main__':
    unittest.main()
