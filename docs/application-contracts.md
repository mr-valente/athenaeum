# Application contracts

Simplified baseline, 9 September 2026. **Current** describes implemented behavior; **target** describes work assigned to later phases. These are operational contracts, not an editorial project catalog.

## Athenaeum

| Concern | Contract |
| --- | --- |
| Purpose | Public notes, project links, and classroom resources |
| Repository / service | `athenaeum` / `athenaeum` |
| Public URL | `https://valentemath.com/`; `www` eventually redirects to the apex |
| Current build | Static Astro output in `dist/`; directory-driven pages and indexes |
| Editorial input | `content/`; Markdown plus frontmatter, referenced assets only |
| Current preview | Local-only Astro dev/preview server, default port 4321 |
| Current container | ARM64 and AMD64 images; Caddy on internal HTTP 8080, non-root UID/GID 10001, read-only compatible; native AMD64 runtime verified |
| Writable data / backups | None for the static site; rebuild from Git and the lockfile |
| Secrets | None for site build or serving |
| Browser state / background jobs | None |
| Health | `GET /_health` returns HTTP 200 and `ok`; Docker healthcheck included; excluded from navigation |
| Sleep policy | Always running; optional Sablier does not manage the site |
| Style | Local build imports `design/` contract 0.1.0; provisional |

Phase B implements directory-driven routing, validated metadata and links, indexes, drafts, local math/fonts, referenced assets, a sitemap, and a production static image. The image requires no secrets, writable directory, or persistent volume. Its HTTP listener is behind the separate Caddy edge in the local ecosystem; public ACME/cloud deployment has not been performed. Native ARM execution remains a deployment acceptance check.

## Quacktuaries integration

The independent sibling repository now supports the prefix through Uvicorn/FastAPI, named route generation, and scoped session cookies. Its pinned Python dependencies and non-root image were tested locally on AMD64. See [local ecosystem](local-ecosystem.md) and Quacktuaries' `docs/deployment.md` for commands.

| Concern | Integration contract |
| --- | --- |
| Repository / service | Independent `quacktuaries` repository / `quacktuaries` |
| Public URL | `/quacktuaries/`; reserve the whole path segment |
| Internal port | 8000; never publish directly on the host |
| Prefix handling | Edge strips the prefix; FastAPI knows its public root path; generated links/forms/assets/redirects must include it |
| Host data | `/srv/athenaeum/apps/quacktuaries/data/` mounted at `/data` |
| Required configuration | `DB_PATH=/data/app.db`, persistent strong `SESSION_SECRET_FILE` (or `SESSION_SECRET`), `ROOT_PATH=/quacktuaries`, `APP_ENV=production`, explicit `FORWARDED_ALLOW_IPS` |
| Session cookie | `quacktuaries_session`, path `/quacktuaries`, host-only, SameSite=Lax and production Secure/HttpOnly |
| Backup / restore | Implemented host tooling: SQLite online snapshot plus secret/config, schema/digest manifest, age encryption, isolated restore validation; host activation pending |
| Health | `GET /_health`: read-only SQLite readiness, 200/503, hidden by the public edge |
| Sleep / updates | Stay awake; operator schedules manual Compose updates outside classroom use and takes a backup first |
| ARM | All pinned dependencies have compatible ARM64 wheels; base image includes ARM64; native ARM runtime verification remains pending |
| Style | Adopt a pinned shared design version later; current appearance is not the source |

The old `quacktuaries.valentemath.com` deployment stays in place while the integration is built and tested. Preserve required data before replacing its ephemeral Cloud Run instance. Phase C implements local routing, cookie changes, persistence mounts, and health checks. Phase D adds backup/recovery and guards. Delivery uses local ARM64 builds, Docker Hub images, and manual Compose updates/compatible version rollback. Cloud activation, final shared style, and live migration remain later work. The new local site links to `/quacktuaries/`; the running Cloud Run deployment is unchanged.

## Future application checklist

For a new app, document its repository/service slug, public prefix, internal port, runtime/architectures, health/readiness behavior, owned writable paths, required secrets, consistent backup/restore hooks, browser-state namespace, jobs, sleep eligibility, and style version. Stateless applications explicitly declare no backup requirement.

App prefixes must not overlap published Markdown routes or another app's prefix. Shared paths on the same domain are one browser origin, not security isolation. Only the edge accepts public traffic; each application owns only its own data. Compose uses explicit Docker Hub image references; the shared local builder publishes version tags. Infrastructure owns cloud permissions and backup storage.

Use [the application skill](../skills/athenaeum-app/SKILL.md) and [implementation plan](implementation-plan.md) for the integration workflow and acceptance criteria. Add the human-facing project listing as Markdown using [the authoring guide](authoring.md); do not copy marketing text into operational configuration.
