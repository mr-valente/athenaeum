# Shared accounts and performance

The always-running `accounts` service owns identity and performance history.
Caddy serves it at `/auth/` and `/account/`. The main website remains static and
public; its Login/Account link opens the overview. A same-origin request to
`/account/status` returns only `signed_in`, with caching disabled, to select the
label. Hosted apps use the private API at
`http://accounts:8000`. Caddy blocks `/internal/*`; no application port is published.

## State and credentials

| Item | Location |
| --- | --- |
| Database | `/srv/athenaeum/apps/accounts/data/app.db` |
| Session secret and Google credentials | `/etc/athenaeum/accounts-session-secret` |
| Image | `valentemath/athenaeum:latest-accounts` |
| Origin | `ACCOUNT_ORIGIN` in private `compose.env` |
| Callback | `/auth/google/callback` |

The private credential file starts as a random session secret. The Google
configurator changes it to JSON with `session_secret`, `google_client_id` and
`google_client_secret`, preserving that original secret. It is included in
encrypted backups. Never print it or commit it.

Accounts mounts registered apps' signing keys read-only. Each app's API key is
HMAC-SHA256 of `athenaeum-account-api-v1:<app-id>` using its own session secret,
encoded as lowercase hexadecimal. Another app's key cannot select its namespace.
Register each new app's key mount and ID in `ACCOUNTS_APP_SECRETS`.

The service joins private `apps` and outbound `default` networks to reach Google.
It joins neither control nor socket networks and has no Sablier labels. Host
status rejects a stopped accounts container.

## Browser identity and ownership

The production cookie `__Host-athenaeum_account` is host-only, Secure, HttpOnly,
SameSite=Lax and scoped to `/`. It holds a random opaque token; the database
stores only its hash. Google sessions last 30 days; shared guests last seven.
Logout revokes sessions server-side. Apps resolve identity on every request.
A separate ten-minute signed cookie holds OAuth state and form CSRF tokens.
Caddy strips cookies from the static website's upstream requests.

Authlib handles authorization-code flow, PKCE, state, nonce and ID-token
verification. Google's stable subject identifies accounts, never email or name.
Google access/refresh tokens are not retained. Forms and explicit linking require
CSRF tokens; return paths must be local. Google login grants no global teacher
privilege: classroom ownership remains app-local.

New local teacher/player profiles receive a shared account link when created with
a resolved identity. Linked profiles recover on another device by account ID.
Existing guest profiles remain unlinked until explicitly saved in the original
browser; its rejoin token is required. Google-owned profiles cannot be transferred.

The overview lets Google users set a display name of up to 60 characters. Google
supplies the initial name; future sign-ins retain the chosen name. Apps use this
name for linked teacher and player profiles and update their labels on the next
request. Names do not reserve identities: teachers and signed-in players can
share a name while retaining separate profiles. One account recovers one seat
per classroom. Guests can reuse teacher names; player names remain protected
within a classroom, and rejoining from the same browser recovers the same seat.

Each app has one Login/Account header link to its local account page. The central
overview has one link per app, pointing to that page. Guest saving remains an
explicit action there, using browser proof.

During an account-service outage, an established classroom seat can continue only
with the same shared cookie and its signed browser account binding. Fresh and
switched identities require service validation. Revocations made while validation
is unavailable take effect when it resumes. Unlinked guest operation remains local.

## Performance API v1

Requests require `X-Athenaeum-App` and `Authorization: Bearer <app-key>`.

- `POST /internal/identity`: `{token}` → `{user}` or `{user: null}`. Unknown,
  expired and revoked tokens are not identities.
- `POST /internal/performance`: `source_id`, `user_id`, positive `revision`,
  `schema_version: 1`, `activity`, timezone-aware `occurred_at`, `metrics` and
  `context`. Each JSON object is bounded to 32 KB. Only Google accounts receive
  persistent history.

The primary key is `(app, source_id)`. Repeated/stale revisions are acknowledged
without replacing newer data. Ownership is immutable. First acceptance creates
an audit record. App servers establish account IDs and metrics, never browsers.

Bernoulli publishes one vote per revealed round with correctness, changes and
sample context. Quacktuaries publishes one completed player run with score,
interval coverage/width, sampling/resource use, policies and scoring settings.
Active-game summaries and unrevealed truths are not exposed. Raw details appear
in the JSON export; the overview displays scalar metrics and context.

The canonical adapter is `accounts/client/ecosystem.py`. Python consumers bundle
identical pinned copies and the account template; integration tests check the
copies. App-owned `performance.py` implements finalized snapshots. Other
frameworks can implement the same contract.

`ecosystem_links` and `ecosystem_outbox` are additive tables. Bernoulli removes
its older name uniqueness constraints at startup in a transaction, preserving
all classroom rows, IDs and references. It checks integrity and references before
committing, rolls back on failure and creates no extra data files. Subsequent
startups leave the schema alone. The normal host deployment takes its verified
encrypted backup before replacing the app.
The outbox changes in the gameplay transaction and
delivers outside it. Retry runs every 15 seconds while awake, startup reconciles
finalized records, and sleeping apps retain pending rows. Acknowledged source
revisions remain on disk. The app account page reports pending counts. A service
outage pauses synchronization without rolling back successful gameplay.
Rejected records remain available for operator review and do not block other
users' results. A corrected snapshot retries with a new revision.

## Delivery and recovery

The existing Fish Athenaeum build has three outputs: website, edge and accounts
at one Athenaeum version. Apps retain independent releases.

Before first rollout, take a verified backup with the installed tooling. Sync
source, then update host tools to create the new directory and secret. Publish
the three Athenaeum images and both updated app images through the manual workflow.
Deploy outside classroom use and finish with `verify`. See
[daily usage](../guides/3-daily-usage.md) and
[Google setup](../development/google-login.md).

Accounts uses SQLite online backups and the encrypted recovery contract.
Classroom backups include links, outbox payloads and delivery revisions. Snapshot
accounts **after** classroom databases: referenced accounts were created before
the app linked them, and account identities are retained. These are sequential
online snapshots. History can include finalized results newer than a restored
classroom snapshot; revision checks keep replay from duplicating or replacing
newer history.

The isolated accounts restore probe needs no network or Google credentials.
It checks health, the overview, integrity and registered tables. Older snapshots
remain accepted but do not contain account history; reinitialize new registrations
deliberately or select the older whole-stack source.

Older app images ignore the additive tables. Preserve account data/keys when
rolling back app images. Removing the accounts registration also requires
compatible host tooling and Compose; unregistered data directories fail backups.

## Local verification

Use a Python environment with `accounts/requirements.lock`, the app dependencies,
and SQLAlchemy 2.0. Then run `python -m unittest discover -s tests -p 'test_*.py'`
and `npm run verify`. Set `ATHENAEUM_TEST_AGE` to `age` for encrypted recovery
checks. The three-service test requires sibling checkouts and local sockets and
uses synthetic identities without a production test-login endpoint. Real Google
and representative school-account testing remains a separate deployment check.

The disposable Docker/HTTPS check is `python tests/accounts_docker_smoke.py`.
Build `athenaeum-accounts:test` from `accounts/Dockerfile`,
`athenaeum-accounts-edge:test` from `docker/edge.Dockerfile`, and
`bernoulli-accounts:test` / `quacktuaries-accounts:test` from their sibling
Dockerfiles first. Run the website build to populate `dist/`, and install the
pinned browser package and Chromium as described in
[local development](../development/local-stack.md). The check uses synthetic
identities and removes its own containers/network; it never starts another
Sablier manager. On Linux, `ATHENAEUM_BROWSER_IMAGE` can select an already
installed image with Chromium's system libraries to run the browser in a
container instead of installing those libraries on the host.
