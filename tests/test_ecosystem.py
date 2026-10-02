"""Real three-service integration with disposable data and synthetic identities.

No production test-login endpoint is introduced. Google identities are fixtures
in the test database; the actual provider flow is covered separately.
"""
import hashlib
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]


class EcosystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.processes, cls.ports, cls.envs = {}, {}, {}
        for name in ('accounts', 'bernoulli', 'quacktuaries'):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                cls.ports[name] = sock.getsockname()[1]
            (cls.directory / name).mkdir()
            (cls.directory / (name + '-secret')).write_text((name + '-fixture-') * 5)
        try:
            for name in ('accounts', 'bernoulli', 'quacktuaries'):
                if name == 'bernoulli':
                    # Exercise a real startup upgrade, including an abandoned
                    # teacher name. IDs, tokens and child references survive.
                    with sqlite3.connect(cls.directory / name / 'app.db') as db:
                        db.executescript('''
                        CREATE TABLE teachers (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
                            rejoin_token VARCHAR(32) NOT NULL, created_at DATETIME, UNIQUE(name));
                        CREATE TABLE players (id VARCHAR PRIMARY KEY, session_id VARCHAR NOT NULL,
                            name VARCHAR NOT NULL, rejoin_token VARCHAR(32) NOT NULL, created_at DATETIME,
                            FOREIGN KEY(session_id) REFERENCES sessions(id),
                            CONSTRAINT uq_session_player_name UNIQUE(session_id, name));
                        INSERT INTO teachers VALUES ('legacy-teacher','Mr. Valente','legacy-private-token','2026-01-01 00:00:00');
                        ''')
                env = dict(os.environ, APP_ENV='production', ROOT_PATH='/' + name,
                           DB_PATH=str(cls.directory / name / 'app.db'), PORT=str(cls.ports[name]),
                           SESSION_SECRET_FILE=str(cls.directory / (name + '-secret')),
                           FORWARDED_ALLOW_IPS='127.0.0.1', PYTHONDONTWRITEBYTECODE='1')
                env.pop('SESSION_SECRET', None)
                if name == 'accounts':
                    import json
                    env.update(ACCOUNT_ORIGIN='https://valentemath.com', ACCOUNTS_APP_SECRETS=json.dumps({
                        app: str(cls.directory / (app + '-secret')) for app in ('bernoulli', 'quacktuaries')}))
                    cwd, module = ROOT, 'accounts'
                else:
                    env.update(ACCOUNT_SERVICE_URL=f'http://127.0.0.1:{cls.ports["accounts"]}', ATHENAEUM_APP_ID=name)
                    cwd, module = ROOT.parent / name, 'app'
                cls.envs[name] = (env, cwd, module)
                cls.start(name)
            with sqlite3.connect(cls.directory / 'accounts/app.db') as db:
                for user in ('alice', 'bob', 'browser'):
                    db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user, 'google', user.title(), user + '@example.test', '2026-10-01T00:00:00+00:00'))
                    db.execute('INSERT INTO account_sessions VALUES (?,?,?)', (hashlib.sha256((user + '-token').encode()).hexdigest(), user, int(time.time()) + 10000))
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def start(cls, name):
        env, cwd, module = cls.envs[name]
        log = open(cls.directory / (name + '.log'), 'a')
        process = subprocess.Popen([sys.executable, '-m', module], cwd=cwd, env=env, stdout=log, stderr=log)
        log.close()
        cls.processes[name] = process
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError(name + ' exited during startup: ' + (cls.directory / (name + '.log')).read_text()[-2000:])
            try:
                if httpx.get(f'http://127.0.0.1:{cls.ports[name]}/_health').status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.05)
        raise RuntimeError(name + ' did not become ready')

    @classmethod
    def stop(cls, name):
        process = cls.processes.pop(name, None)
        if process:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    @classmethod
    def tearDownClass(cls):
        for name in list(cls.processes):
            cls.stop(name)
        cls.temp.cleanup()

    def request(self, app, method, path, cookies, data=None, payload=None):
        headers = {'Host': 'valentemath.com', 'X-Forwarded-Proto': 'https',
                   'Cookie': '; '.join(k + '=' + v for k, v in cookies.items())}
        response = httpx.request(method, f'http://127.0.0.1:{self.ports[app]}' + path,
                                 headers=headers, data=data, json=payload, timeout=8)
        for cookie in response.cookies.jar:
            cookies[cookie.name] = cookie.value
        return response

    def create_class(self, app, name, cookies):
        response = self.request(app, 'POST', '/admin/login', cookies, {'teacher_name': name})
        self.assertEqual(response.status_code, 303, response.text)
        create = '/x/flip-flop/create' if app == 'bernoulli' else '/admin/session/create'
        response = self.request(app, 'POST', create, cookies, {})
        self.assertEqual(response.status_code, 303, response.text)
        session_id = response.headers['location'].rsplit('/', 1)[-1]
        with sqlite3.connect(self.directory / app / 'app.db') as db:
            code = db.execute('SELECT join_code FROM sessions WHERE id=?', (session_id,)).fetchone()[0]
        return session_id, code

    def join(self, app, code, name, cookies):
        response = self.request(app, 'POST', '/session/join', cookies, {'join_code': code, 'player_name': name})
        self.assertEqual(response.status_code, 303, response.text)

    def test_shared_identity_recovery_switching_guest_linking_and_result_delivery(self):
        teacher, student = {}, {'__Host-athenaeum_account': 'alice-token'}
        classrooms = {}
        for app in ('bernoulli', 'quacktuaries'):
            sid, code = self.create_class(app, 'Guest teacher', teacher)
            classrooms[app] = (sid, code)
            self.join(app, code, 'Alice', student)
            # Fresh browser, only shared identity; app cookie is absent.
            fresh = {'__Host-athenaeum_account': 'alice-token'}
            path = f'/x/flip-flop/s/{sid}' if app == 'bernoulli' else f'/s/{sid}'
            self.assertEqual(self.request(app, 'GET', path, fresh).status_code, 200)
            switched = dict(student, __Host_ignored='unused')
            switched['__Host-athenaeum_account'] = 'bob-token'
            self.assertEqual(self.request(app, 'GET', path, switched).status_code, 303)
            signed_out = {k: v for k, v in student.items() if k != '__Host-athenaeum_account'}
            self.assertEqual(self.request(app, 'GET', path, signed_out).status_code, 303)

        sid, code = classrooms['bernoulli']
        self.request('bernoulli', 'POST', f'/x/flip-flop/t/{sid}/round', teacher,
                     {'p': '.5', 'n': '20', 'force': 'true', 'forced_heads': '10'})
        self.request('bernoulli', 'POST', f'/x/flip-flop/t/{sid}/round/open', teacher, {})
        response = self.request('bernoulli', 'POST', f'/x/flip-flop/s/{sid}/vote', student, payload={'choice': 'fair'})
        self.assertEqual(response.status_code, 200, response.text)
        with sqlite3.connect(self.directory / 'bernoulli/app.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ecosystem_outbox').fetchone()[0], 0)
        self.request('bernoulli', 'POST', f'/x/flip-flop/t/{sid}/round/lock', teacher, {})
        self.request('bernoulli', 'POST', f'/x/flip-flop/t/{sid}/round/reveal', teacher, {})

        sid, code = classrooms['quacktuaries']
        guest = {}
        self.join('quacktuaries', code, 'Guest Alice', guest)
        # A permanently rejected record is retained without starving other users.
        import json
        rejected = {'source_id': 'synthetic-rejection', 'user_id': 'nonexistent-account',
                    'activity': 'quacktuaries', 'occurred_at': '2026-10-01T00:00:00+00:00',
                    'metrics': {}, 'context': {}}
        with sqlite3.connect(self.directory / 'quacktuaries/app.db') as db:
            db.execute('INSERT INTO ecosystem_outbox(id,revision,delivered,payload) VALUES (?,?,?,?)',
                       ('synthetic-rejection', 1, 0, json.dumps(rejected)))
        self.request('quacktuaries', 'POST', f'/admin/session/{sid}/start', teacher, {})
        self.request('quacktuaries', 'POST', f'/admin/session/{sid}/end', teacher, {})
        guest['__Host-athenaeum_account'] = 'alice-token'
        page = self.request('quacktuaries', 'GET', '/account', guest)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
        local_id = re.search(r'name="local_id" value="([^"]+)"', page.text)[1]
        forged = self.request('quacktuaries', 'POST', '/account/link', {'__Host-athenaeum_account': 'bob-token'},
                              {'role': 'player', 'local_id': local_id, 'csrf_token': csrf})
        self.assertEqual(forged.status_code, 403)
        response = self.request('quacktuaries', 'POST', '/account/link', guest,
                                {'role': 'player', 'local_id': local_id, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 303, response.text)

        # Test crash-safe retry: queue survives replacement and accounts downtime.
        self.stop('accounts')
        self.stop('bernoulli')
        self.start('bernoulli')
        with sqlite3.connect(self.directory / 'bernoulli/app.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ecosystem_outbox').fetchone()[0], 1)
        self.start('accounts')
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with sqlite3.connect(self.directory / 'accounts/app.db') as db:
                count = db.execute("SELECT count(*) FROM performance WHERE user_id='alice'").fetchone()[0]
            if count == 3:
                break
            time.sleep(.3)
        self.assertEqual(count, 3, 'Expected one Bernoulli vote and two Quacktuaries classroom results')
        with sqlite3.connect(self.directory / 'quacktuaries/app.db') as db:
            self.assertEqual(db.execute("SELECT last_error,delivered FROM ecosystem_outbox WHERE id='synthetic-rejection'").fetchone(),
                             ('account-service-http-422', 0))
        response = self.request('accounts', 'GET', '/account/export', student)
        self.assertEqual(len(response.json()['performance']), 3)
        response = self.request('accounts', 'GET', '/account/export', {'__Host-athenaeum_account': 'bob-token'})
        self.assertEqual(response.json()['performance'], [])
        # Persistent ownership cannot be reassigned using an old browser token.
        guest['__Host-athenaeum_account'] = 'bob-token'
        response = self.request('quacktuaries', 'POST', '/account/link', guest,
                                {'role': 'player', 'local_id': local_id, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 409)

    def test_vendored_adapter_matches_contract(self):
        for app in ('bernoulli', 'quacktuaries'):
            self.assertEqual((ROOT / 'accounts/client/ecosystem.py').read_bytes(),
                             (ROOT.parent / app / 'app/ecosystem.py').read_bytes())
            self.assertEqual((ROOT / 'accounts/client/account.html').read_bytes(),
                             (ROOT.parent / app / 'app/templates/account.html').read_bytes())

    def set_name(self, cookies, name):
        page = self.request('accounts', 'GET', '/account/', cookies)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
        response = self.request('accounts', 'POST', '/account/profile', cookies,
                                {'name': name, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 303, response.text)

    def test_display_names_are_reusable_without_sharing_classrooms_or_seats(self):
        alice = {'__Host-athenaeum_account': 'alice-token'}
        bob = {'__Host-athenaeum_account': 'bob-token'}
        try:
            self.set_name(alice, 'Mr. Valente')
            self.set_name(bob, 'Mr. Valente')
            for app in ('bernoulli', 'quacktuaries'):
                abandoned, _ = self.create_class(app, 'Mr. Valente', {})
                sid, code = self.create_class(app, 'ignored form value', alice)
                other, _ = self.create_class(app, 'ignored form value', bob)
                teacher_path = f'/x/flip-flop/t/{abandoned}' if app == 'bernoulli' else f'/admin/s/{abandoned}'
                self.assertEqual(self.request(app, 'GET', teacher_path, alice).status_code, 303)
                teacher_path = f'/x/flip-flop/t/{other}' if app == 'bernoulli' else f'/admin/s/{other}'
                self.assertEqual(self.request(app, 'GET', teacher_path, alice).status_code, 303)
                self.join(app, code, 'ignored form value', alice)
                self.join(app, code, 'ignored form value', bob)
                fresh = {'__Host-athenaeum_account': 'alice-token'}
                self.assertEqual(self.request(app, 'POST', '/session/join', fresh, {'join_code': code}).status_code, 303)
                with sqlite3.connect(self.directory / app / 'app.db') as db:
                    players = db.execute('SELECT id,name FROM players WHERE session_id=?', (sid,)).fetchall()
                    self.assertEqual(len(players), 2)
                    self.assertEqual([name for _, name in players], ['Mr. Valente', 'Mr. Valente'])
                guest = {}
                self.join(app, code, 'Guest learner', guest)
                self.assertEqual(self.request(app, 'POST', '/session/join', {},
                    {'join_code': code, 'player_name': 'Guest learner'}).status_code, 400)
                self.join(app, code, 'Renamed guest', guest)
                with sqlite3.connect(self.directory / app / 'app.db') as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM players WHERE session_id=?', (sid,)).fetchone()[0], 3)
                self.set_name(alice, 'Ms. Rowan')
                path = f'/x/flip-flop/s/{sid}' if app == 'bernoulli' else f'/s/{sid}'
                self.assertIn('Ms. Rowan', self.request(app, 'GET', path, fresh).text)
                with sqlite3.connect(self.directory / app / 'app.db') as db:
                    names = db.execute('SELECT p.name FROM players p JOIN ecosystem_links l ON l.local_id=p.id '
                                       "WHERE l.role='player' AND l.account_id='alice' AND p.session_id=?", (sid,)).fetchall()
                    self.assertEqual(names, [('Ms. Rowan',)])
                self.set_name(alice, 'Mr. Valente')
        finally:
            self.set_name(alice, 'Alice')
            self.set_name(bob, 'Bob')

    def test_concurrent_account_joins_recover_one_profile_and_one_seat(self):
        from concurrent.futures import ThreadPoolExecutor
        with sqlite3.connect(self.directory / 'accounts/app.db') as db:
            db.execute('INSERT INTO users VALUES (?,?,?,?,?)', ('parallel', 'google', 'Parallel', 'parallel@example.test', '2026-10-01T00:00:00+00:00'))
            db.execute('INSERT INTO account_sessions VALUES (?,?,?)',
                       (hashlib.sha256(b'parallel-token').hexdigest(), 'parallel', int(time.time()) + 10000))
        for app in ('bernoulli', 'quacktuaries'):
            def login(_):
                return self.request(app, 'POST', '/admin/login', {'__Host-athenaeum_account': 'parallel-token'}, {}).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(login, range(2))), [303, 303])
            teacher = {}
            sid, code = self.create_class(app, 'Concurrent class', teacher)
            def join(_):
                return self.request(app, 'POST', '/session/join', {'__Host-athenaeum_account': 'parallel-token'}, {'join_code': code}).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(join, range(2))), [303, 303])
            with sqlite3.connect(self.directory / app / 'app.db') as db:
                self.assertEqual(db.execute("SELECT count(*) FROM ecosystem_links WHERE account_id='parallel' AND role='teacher'").fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT count(*) FROM players WHERE session_id=?', (sid,)).fetchone()[0], 1)

    def test_teacher_profiles_recover_without_names_and_keep_classroom_ownership(self):
        for app in ('bernoulli', 'quacktuaries'):
            sessions = []
            for index in range(2):
                browser = {}
                sid, _ = self.create_class(app, f'Guest teacher {index}', browser)
                sessions.append(sid)
                browser['__Host-athenaeum_account'] = 'alice-token'
                page = self.request(app, 'GET', '/account', browser)
                csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
                local_id = re.search(r'name="local_id" value="([^"]+)"', page.text)[1]
                self.assertEqual(self.request(app, 'POST', '/account/link', browser,
                    {'role': 'teacher', 'local_id': local_id, 'csrf_token': csrf}).status_code, 303)
            fresh = {'__Host-athenaeum_account': 'alice-token'}
            dashboard = self.request(app, 'GET', '/admin/dashboard', fresh)
            self.assertEqual(dashboard.status_code, 200)
            for sid in sessions:
                self.assertIn(sid, dashboard.text)
                path = f'/x/flip-flop/t/{sid}' if app == 'bernoulli' else f'/admin/s/{sid}'
                self.assertEqual(self.request(app, 'GET', path, fresh).status_code, 200)
                self.assertEqual(self.request(app, 'GET', path, {'__Host-athenaeum_account': 'bob-token'}).status_code, 303)


if __name__ == '__main__':
    unittest.main()
