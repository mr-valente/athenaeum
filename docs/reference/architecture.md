# Architecture and application contracts

One Ubuntu 26.04 ARM VM runs Caddy, a static website, Quacktuaries, Bernoulli, SRS, and a shared account service. Cloudflare supplies DNS; Caddy terminates HTTPS. Images are built on the workstation, published to Docker Hub, and deployed manually with `athenaeumctl`. The hourly backup and the quarter-hourly host report are the scheduled Athenaeum jobs.

```text
Internet → Caddy edge
             ├─ /                → Athenaeum static website
             ├─ /auth/, /account/ → shared accounts
             ├─ /quacktuaries/   → Quacktuaries
             ├─ /bernoulli/      → Bernoulli
             └─ /srs/            → SRS

/srv/athenaeum → persistent app data and Caddy state
Oracle Object Storage ← encrypted, verified database/key/config snapshots
```

## Services

| Service | Image | Internal ports | Persistent state |
| --- | --- | --- | --- |
| `edge` | `valentemath/athenaeum:latest-edge` | HTTP 8080, HTTPS 8443, health 8081 | `/srv/athenaeum/edge/{data,config}` |
| `athenaeum` | `valentemath/athenaeum:latest` | HTTP 8080 | None; rebuild from Git |
| `accounts` | `valentemath/athenaeum:latest-accounts` | HTTP 8000 | `/srv/athenaeum/apps/accounts/data/app.db` and private credentials |
| `quacktuaries` | `valentemath/quacktuaries:latest-athenaeum` | HTTP 8000 | `/srv/athenaeum/apps/quacktuaries/data/app.db` and persistent signing key |
| `bernoulli` | `valentemath/bernoulli:latest-athenaeum` | HTTP 8000 | `/srv/athenaeum/apps/bernoulli/data/app.db` and persistent signing key |
| `srs` | `valentemath/srs:latest-athenaeum` | HTTP 8000 | `/srv/athenaeum/apps/srs/data/app.db` and persistent signing key |
| `sablier` | `sablierapp/sablier:1.18.0` | Private control HTTP 10000 | None; config/theme in Git, idle timers in memory |
| `socket-proxy` | `lscr.io/linuxserver/socket-proxy:3.4.4-r0-ls97` | Private Docker API HTTP 2375 | None |

Only Caddy publishes host ports 80/443. The website, edge and apps run as UID/GID 10001, with read-only root filesystems, bounded logs/memory, health checks, and dropped capabilities. Sablier (`sablierapp/sablier:1.18.0`) and its socket proxy (`lscr.io/linuxserver/socket-proxy`) are two further infrastructure services under the same runtime restrictions. Only the proxy mounts the Docker socket, as root; it forwards Sablier the container list, inspect, start and stop calls over the private `socket` network and denies everything else, so Sablier itself runs without the socket. Release builds target ARM64. Native development images use the workstation architecture.

The private `apps` network connects trusted owned applications; Docker supplies names and addresses. Caddy also joins the outbound network. Apps receive neither a Docker socket nor OCI credentials. The host firewall denies container access to Oracle metadata; the host's instance principal authenticates backup storage requests.

Caddy and Sablier additionally share the private `control` network, and Sablier and the socket proxy the private `socket` network; apps can reach neither Sablier's API nor the Docker API. Each hosted app has its own Sablier group and sleeps after its session tier's idle period without requests (72 hours for a light app, down to one hour for the heaviest). The website is never managed by Sablier. See [on-demand applications](sablier.md) for loading-page routing, security, session behavior and verification.

Caddy redirects `www` to the apex, preserves queries on each app's slash redirect (`/quacktuaries`, `/bernoulli`, `/srs`), and strips the prefix upstream. Each app generates prefixed links, forms, assets and redirects, and hides its `/_health` probe from the public route. Cookies are per app (`quacktuaries_session`, `bernoulli_session`, `srs_session`): host-only, scoped to the app's prefix, HttpOnly, SameSite=Lax, and Secure in production. Same-domain apps share a browser origin.

The `accounts` infrastructure service owns shared login and performance history. It stays awake, joins the outbound network to reach Google, and stores `/srv/athenaeum/apps/accounts/data/app.db`. Its private credential file is `/etc/athenaeum/accounts-session-secret`; its image is `valentemath/athenaeum:latest-accounts`. The shared root cookie complements the per-app classroom cookies. See [shared accounts](accounts.md) and [Google setup](../development/google-login.md).

## Files and ownership

| Location | Responsibility |
| --- | --- |
| `content/` | Markdown pages, editorial project catalog, referenced downloads |
| `src/`, `design/`, `style.md` | Site renderer and presentation |
| `compose.yaml` | Production services, image defaults, mounts, networks and limits |
| `compose.local.yaml`, `ops/local-stack` | Local builds and isolated HTTPS development |
| `deploy/caddy/` | Public routing and TLS settings, baked into the edge image |
| `docker/compose.yaml` | Site, edge and accounts release builds |
| `ops/athenaeum_ops/` | Command center, deployment, verification, backup and restore logic |
| `ops/runbook/` | Initial host setup and recovery-drill helpers |
| `/opt/athenaeum/stack` on the VM | Git checkout of public source and Compose |
| `/opt/athenaeum/operations` | Installed host code and its Python environment |
| `/etc/athenaeum/compose.env` | Private host settings and optional image overrides |
| `/etc/athenaeum/recovery.json` | Disk UUID, backup settings and the host report's object name |
| `/etc/athenaeum/quacktuaries-session-secret`, `/etc/athenaeum/bernoulli-session-secret`, `/etc/athenaeum/srs-session-secret`, `/etc/athenaeum/accounts-session-secret` | Persistent signing keys and account credentials, mode 0600, owner 10001 |
| `/var/lib/athenaeum` | Private host operation lock, backup status and installer rollback files |
| `/srv/athenaeum` | Exact-UUID mounted data volume |

`repo sync` validates and fast-forwards a clean checkout; it does not change containers or installed tools. `self update` refreshes installed host tools without restarting Docker. `docker pull --deploy` holds the shared lock across a verified backup, image pull and replacement. Image rollback leaves the database unchanged; schema changes need a recovery plan.

The host report (`ops/athenaeum_ops/report.py`, `athenaeum-report.timer`) publishes disk usage, backup freshness and container states to one object outside the backup prefix, with the same instance principal, for an external monitor; it probes the operation lock without waiting so it never delays a backup. See [daily usage](../guides/3-daily-usage.md#host-report).

The UUID guard prevents Docker from starting against a missing data disk. Bind mounts refuse missing source directories. The apps and account service use SQLite; their databases are captured with SQLite's online backup API and, with their signing keys, settings, and image metadata, form one recovery point. Certificates are reissued on a replacement host. See [backups and restores](../guides/5-backup-and-recovery.md).

## Adding an application

Each app owns its repository, Dockerfile, Docker Hub repository, Fish build entry, and version counter. One app build publishes a standalone `latest` image and a hosted `latest-athenaeum` image, with retained version tags. Athenaeum builds the website, edge, and account service.

Record the app's service name, URL prefix, internal port, health behavior, data/secret paths, browser-state namespace, background work, and backup/restore method. Reserve its prefix in `deploy/reserved-paths.json`; add its Caddy routes, Compose service and Markdown project page. Keep stateless apps explicitly stateless.

Host tooling registers stateful apps in `ops/athenaeum_ops/common.py` (`APPS`): the entry names the app's signing-key setting, public prefix and required SQLite tables, and every service list, preflight check, Compose validation, snapshot, restore and verification probe derives from it. A restore-test probe per app lives in `runtime.py`, and the local stack and host installer keep their own key/directory lists. An unregistered data directory still makes the backup fail closed. Use the [app-creation skill](../../skills/athenaeum-app/SKILL.md).

The shared CSS contract is defined in [style.md](../../style.md). Athenaeum uses the dark After hours theme; other applications adopt a pinned copy explicitly. SRS bundles a pinned copy; neither classroom app has adopted it yet.
