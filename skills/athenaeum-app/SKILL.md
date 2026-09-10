---
name: athenaeum-app
description: Create or integrate an independently deployable Athenaeum application, including its image variants, URL prefix, persistence, recovery, shared style and Markdown project link.
---

# Athenaeum apps

Locate the Athenaeum checkout from the prompt or sibling `athenaeum/`. Read its `README.md`, `docs/reference/architecture.md`, `style.md`, and current Compose/Caddy files. The numbered guides describe the current system: 1 provisions Oracle, 2 configures a host, 3 covers daily commands, and 4 covers backup/recovery. Preserve unrelated work and keep deployment within the user's authorized scope.

## Integrate the application

Choose the framework that fits the app and keep it independently deployable. Add:

- A service in `compose.yaml` on the private `apps` network, without a published host port. Use Docker service names and assigned addresses. Add a native development build to `compose.local.yaml` when useful.
- Caddy's `/<slug>` slash redirect and `handle_path /<slug>/*` route. Caddy strips the prefix upstream; the app must generate prefixed links, forms, redirects, assets and API URLs. Reserve the path in `deploy/reserved-paths.json`.
- A Markdown page in `content/projects/`, the editorial project catalog.
- Explicit non-root ownership, health checks, resource/log limits, read-only root filesystem where supported, and graceful shutdown.

Give browser cookies unique names, the app's path, and production security flags. Same-domain paths share a browser origin, and owned apps are trusted network peers. Apps serving untrusted executable content need a separate origin/boundary. Ordinary apps receive neither Docker nor OCI credentials.

Apply the current shared CSS contract from `design/` and the decisions in `style.md`. Its visual specification remains provisional; do not copy Quacktuaries' appearance or invent a final theme. Bundle a pinned copy of shared assets in each consumer rather than fetching mutable styles at runtime.

## Own the image and release

Follow `docs/development/image-builds.md`. Each app owns its source repository, Dockerfile/Compose recipe, Docker Hub repository (`valentemath/<app>`), shared Fish `builds.yaml` entry and SemVer record in `versions.txt`. Releasing it must not build or increment Athenaeum or another app. Athenaeum's `docker/compose.yaml` builds only its website and edge.

Use Tailgate's multiple-output pattern: one `build <app>` builds and publishes both variants at the same app version:

```yaml
outputs:
  standalone:
    source_tag: latest
    tags: latest {app}
  athenaeum:
    source_tag: latest-athenaeum
    tags: latest-athenaeum {app}-athenaeum
```

The standalone image works at the root path and is the default Dockerfile target. The hosted target adds actual Athenaeum defaults/assets, not merely a tag. Quacktuaries' own `docker/compose.yaml` and Dockerfile provide the example. Runtime secrets, volumes, domain and proxy trust remain deployment configuration. Both fixed image lines publish together; no separate `--tag-mod` build is needed. Target ARM64 for Oracle and keep native development behavior intact.

## Register state and host checks

Put persistent data under `/srv/athenaeum/apps/<slug>/`, mounted at `/data` or the app's documented equivalent. Preserve the exact-UUID disk guard and refusal to create missing bind directories. A stateless app declares Git/image rebuilding as its recovery method.

For stateful apps, read `docs/guides/4-backup-and-recovery.md` and implement a consistent snapshot and isolated restore. Use SQLite's backup API or the database's own export, never a copy of a live database file. Register uploads and required keys too.

The host code registers stateful SQLite apps in `ops/athenaeum_ops/common.py` (`APPS`): a config key for the app's signing-key path (with a `/etc/athenaeum/<slug>-session-secret` default), its public prefix and its required tables. Service lists, preflight, Compose validation (`<slug>_session` secret, `apps/<slug>/data` mount), snapshot manifests, restore validation, installer key/directory creation and the HTTPS verification probes all derive from that entry. Add a per-app restore-test probe to `runtime.py`, the key/directory names to `ops/local-stack`, `<SLUG>_IMAGE`/`<SLUG>_SECRET_FILE` to `deploy/production.env.example`, and cover the app in the tests (recovery fixtures, installer, local stack, runbook, browser check). Quacktuaries and Bernoulli are the two examples. An app that is not SQLite-backed needs its own hook, manifest entry and restore path. Do not bypass the existing fail-closed checks: merely creating a new app data directory is insufficient and stops the current backup.

## Verify and deliver

Check independent builder dry runs and both image lines. Verify standalone root-path behavior and a complete hosted workflow, including redirects, assets, cookies and persisted records across replacement. For stateful apps, prove that a backup restores in isolation. Report native ARM or live checks accurately; use synthetic data for local testing.

Source changes are committed/pushed on the workstation, then synced with `athenaeumctl repo sync`. Host-tool changes require `athenaeumctl self update`; Compose-only changes use `docker deploy`; published image changes use `docker pull --deploy`. Finish with `athenaeumctl verify`. For a new app, that order also creates its key and data directory before the first deployment; see "Add a registered app" in `docs/guides/3-daily-usage.md`. These deployment commands take a backup first; schedule them outside active classroom use. See `docs/guides/3-daily-usage.md`.

Keep incompatible schema changes explicit and preserve compatible image versions for recovery. Add unattended migrations or image-update automation only if requested. Update the architecture contract and relevant guide when behavior changes; keep operational documentation current, without phase histories or duplicated upgrade instructions. Hand off the public URL, state/secret paths, required settings, validation and actual deployment status.
