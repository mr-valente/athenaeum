# Architecture and application contracts

One Ubuntu 26.04 ARM VM runs Caddy, a static website, and Quacktuaries. Cloudflare supplies DNS; Caddy terminates HTTPS. Images are built on the workstation, published to Docker Hub, and deployed manually with `athenaeumctl`. The hourly backup is the scheduled Athenaeum job.

```text
Internet → Caddy edge
             ├─ /                → Athenaeum static website
             └─ /quacktuaries/   → Quacktuaries

/srv/athenaeum → persistent app data and Caddy state
Oracle Object Storage ← encrypted, verified database/key/config snapshots
```

## Services

| Service | Image | Internal ports | Persistent state |
| --- | --- | --- | --- |
| `edge` | `valentemath/athenaeum:latest-edge` | HTTP 8080, HTTPS 8443, health 8081 | `/srv/athenaeum/edge/{data,config}` |
| `athenaeum` | `valentemath/athenaeum:latest` | HTTP 8080 | None; rebuild from Git |
| `quacktuaries` | `valentemath/quacktuaries:latest-athenaeum` | HTTP 8000 | `/srv/athenaeum/apps/quacktuaries/data/app.db` and persistent signing key |

Only Caddy publishes host ports 80/443. All three containers run as UID/GID 10001, with read-only root filesystems, bounded logs/memory, health checks, and dropped capabilities. Release builds target ARM64. Native development images use the workstation architecture.

The private `apps` network connects trusted owned applications; Docker supplies names and addresses. Caddy also joins the outbound network. Apps receive neither a Docker socket nor OCI credentials. The host firewall denies container access to Oracle metadata; the host's instance principal authenticates backup storage requests.

Caddy redirects `www` to the apex, preserves queries on the `/quacktuaries` slash redirect, and strips that prefix upstream. Quacktuaries generates prefixed links, forms, assets and redirects. Its `quacktuaries_session` cookie is host-only, scoped to `/quacktuaries`, HttpOnly, SameSite=Lax, and Secure in production. Same-domain apps share a browser origin.

## Files and ownership

| Location | Responsibility |
| --- | --- |
| `content/` | Markdown pages, editorial project catalog, referenced downloads |
| `src/`, `design/`, `style.md` | Site renderer and presentation |
| `compose.yaml` | Production services, image defaults, mounts, networks and limits |
| `compose.local.yaml`, `ops/local-stack` | Local builds and isolated HTTPS development |
| `deploy/caddy/` | Public routing and TLS settings, baked into the edge image |
| `docker/compose.yaml` | Site/edge release builds |
| `ops/athenaeum_ops/` | Command center, deployment, verification, backup and restore logic |
| `ops/runbook/` | Initial host setup and recovery-drill helpers |
| `/opt/athenaeum/stack` on the VM | Git checkout of public source and Compose |
| `/opt/athenaeum/operations` | Installed host code and its Python environment |
| `/etc/athenaeum/compose.env` | Private host settings and optional image overrides |
| `/etc/athenaeum/recovery.json` | Disk UUID and backup settings |
| `/etc/athenaeum/quacktuaries-session-secret` | Persistent signing key, mode 0600, owner 10001 |
| `/var/lib/athenaeum` | Private host operation lock, backup status and installer rollback files |
| `/srv/athenaeum` | Exact-UUID mounted data volume |

`repo sync` validates and fast-forwards a clean checkout; it does not change containers or installed tools. `self update` refreshes installed host tools without restarting Docker. `docker pull --deploy` holds the shared lock across a verified backup, image pull and replacement. Image rollback leaves the database unchanged; schema changes need a recovery plan.

The UUID guard prevents Docker from starting against a missing data disk. Bind mounts refuse missing source directories. Quacktuaries uses SQLite's online backup API; its database, signing key, settings, and image metadata form one recovery point. Certificates are reissued on a replacement host. See [backups and restores](../guides/4-backup-and-recovery.md).

## Adding an application

Each app owns its repository, Dockerfile, Docker Hub repository, Fish build entry, and version counter. One app build publishes a standalone `latest` image and a hosted `latest-athenaeum` image, with retained version tags. Athenaeum builds only the website and edge.

Record the app's service name, URL prefix, internal port, health behavior, data/secret paths, browser-state namespace, background work, and backup/restore method. Reserve its prefix in `deploy/reserved-paths.json`; add its Caddy routes, Compose service and Markdown project page. Keep stateless apps explicitly stateless.

Host tooling currently enumerates three services and one SQLite application. When adding a service, update status/verification/log choices and relevant tests. A stateful app also needs explicit preflight, snapshot, manifest and restore support; merely adding a data directory makes the current backup fail closed. Use the [app-creation skill](../../skills/athenaeum-app/SKILL.md).

The shared CSS contract is defined in [style.md](../../style.md). Athenaeum uses the dark After hours theme; other applications adopt a pinned copy explicitly.
