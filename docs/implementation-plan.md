# Athenaeum implementation plan

Phases A–E are implemented locally. This revision replaces the earlier platform-heavy specification with the smaller architecture in [README](../README.md). Cloud deployment and migration remain pending.

## Build this

A directory-driven Markdown website at `valentemath.com`, its own static container, and independent project containers under stable URL prefixes. Start with `/quacktuaries/` and public AP Statistics material. Use one Oracle ARM VM, mounted block storage, Caddy HTTPS, Cloudflare DNS, and private Oracle object backups.

Keep the site and Quacktuaries running. Add Sablier only if measured resource use later justifies it. No CMS, dashboard, shared authentication, orchestrator, release-asset service, or generic app platform is needed.

## Files that matter

| File/directory | Responsibility |
| --- | --- |
| `content/` | All editorial content; Markdown and referenced assets |
| `src/`, `design/`, `style.md` | Static site and shared presentation |
| `compose.yaml` | Three services, data mounts, private app network |
| `compose.local.yaml`, `ops/local-stack` | Local builds and HTTPS testing |
| `deploy/caddy/` | Public routes and TLS configuration |
| `deploy/production.env.example` | Three ordinary deployment settings |
| `deploy/recovery.example.json` | Disk and backup settings |
| `.github/workflows/images.yml`, `ci/images.*` | Native images and Docker Hub publication |
| `ops/` | Backup, restore, mount guards, and classroom-safe image updates |

## Preserve these behaviors

**Content:** navigation and project listings come only from `content/`. Support math, print, downloads, deep links, and drafts. Exclude drafts and draft-only assets from production output. Fail builds for broken internal links, invalid metadata, path traversal, and reserved app URLs. Empty content builds an honest empty landing page. Do not invent teaching material or personal claims.

**Routing:** Caddy terminates HTTPS and strips an app's prefix; the app generates links, forms, redirects, assets, and browser requests with its configured public prefix. Keep `/quacktuaries` → `/quacktuaries/` query-preserving. Cookies use unique names, path scope, and production secure flags. Same-domain app paths share an origin.

**Persistence:** app-owned data lives under `/srv/athenaeum/apps/<slug>/`. Do not create a new empty database when the block filesystem is missing. Keep the UUID guard, explicit existing bind paths, persistent session key, and non-root app processes. Apps get no Docker socket or cloud credentials. Only Caddy publishes ports.

**Backups:** preserve the existing SQLite online snapshot, encryption, upload verification, bounded retention, and isolated restore. Back up signing keys and configuration with data. Keep the age identity outside the VM except during a restore. Object storage is a backup destination, not a live filesystem. Before adding uploads or another database, implement its actual consistency/restore needs.

**Delivery:** push to GitHub; CI tests native AMD64 and ARM64, then publishes Docker Hub indexes. The host checks every 15 minutes, fixes the image digest, and updates only the affected app. Protect active classrooms, back up first, check health/HTTPS, and retain a compatible previous image. Use systemd for scheduling and the existing pause command. Schema-changing updates require a maintenance task; never restore old data automatically to make an image rollback succeed.

**Networks:** owned apps share one private Docker network; Docker assigns addresses and resolves service names. Treat the apps as trusted peers. Untrusted apps or executable user content need a separately considered network/origin boundary. Do not add manual IP planning for routine apps. [Compose networking](https://docs.docker.com/compose/how-tos/networking/).

**Style:** select the restrained, minimal, slightly whimsical developer-site style in `style.md`, then apply shared CSS to Athenaeum and Quacktuaries. Keep frameworks independent. Current Quacktuaries styling is not the reference. Use local assets and accessible semantic UI; final style is still pending.

## Remaining sequence

| Phase | Work | Acceptance |
| --- | --- | --- |
| F — presentation | Select style, apply shared CSS to both apps | Keyboard, mobile, charts/math, and print review |
| G — Oracle deployment | Follow the operator guide; publish images and configure the VM | Native ARM startup, HTTPS, reboot persistence, metadata isolation, backup download and isolated restore |
| H — migration | Export required Cloud Run records, import and validate, then perform the agreed cutover | Record counts, teacher/student workflows, old links, and recovery path verified |
| I — optional sleeping | Consider Sablier only after measuring need | Wake/reboot behavior and active class protection verified |

Phase G resource sizing starts at 2 OCPUs / 12 GB, subject to actual tenancy allowance and capacity. Verify Oracle limits when provisioning; do not upgrade the account or allocate paid resources by assumption.

Keep the Cloud Run subdomain running until migration is accepted. Cloud Run does not provide a normal SSH/Docker-volume export path; preserve required records through a supported export before replacing its ephemeral instance.

## Adding an app

Use [the app skill](../skills/athenaeum-app/SKILL.md). Add its Compose service, Caddy route, reserved path, and Markdown project page. Start with ordinary container deployment. Extend backup/update handling only for the app's actual state and activity requirements; current host automation knows Quacktuaries, not an arbitrary database framework.

## Verification and handoff

Run tests appropriate to the changes: content/build tests for publishing, real prefixed workflows for routing, and isolated backup/update/rollback drills for operations. Use synthetic data. Preserve unrelated edits and `docs/hosting-research.pdf`.

Keep the README, operator guide/PDF, and app skill consistent with actual commands. Report local tests, publication, host installation, native ARM execution, and migration separately. Inputs still needed: account/disk inventory, actual repository and image owners, recovery-key custody, selected style, and which Cloud Run data to migrate.
