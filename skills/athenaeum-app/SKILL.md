---
name: athenaeum-app
description: Create or integrate an independently deployable Athenaeum application, including its image variants, URL prefix, persistence, recovery, shared style and Markdown project link.
---

# Athenaeum apps

Locate the Athenaeum checkout from the prompt or sibling `athenaeum/`. Read its `README.md`, `docs/reference/architecture.md`, `style.md`, and current Compose/Caddy files. The numbered guides describe the current system: 1 provisions Oracle, 2 configures a host, 3 covers daily commands, and 4 covers backup/recovery. Preserve unrelated work and keep deployment within the user's authorized scope.

## Integrate the application

Choose the framework that fits the app and keep it independently deployable. Add:

- A service in `compose.yaml` on the private `apps` network, without a published host port. Use Docker service names and assigned addresses. Add a native development build to `compose.local.yaml` when useful.
- An `import <tier> /<slug> <service> <DisplayName>` entry in `deploy/caddy/routes.caddy`, where the tier is a session-tier snippet from `apps.caddy` (see the table below) wrapping the shared `app` snippet (currently port 8000; parameterize it if another app needs a different port). It preserves slash-redirect queries, hides `/_health`, wakes only this app, then strips the prefix upstream. The app must generate prefixed links, forms, redirects, assets and API URLs. Reserve the path in `deploy/reserved-paths.json`.
- A Markdown page in `content/projects/`, the editorial project catalog.
- Explicit non-root ownership, health checks, resource/log limits, read-only root filesystem where supported, and graceful shutdown.

Give browser cookies unique names, the app's path, and production security flags. Same-domain paths share a browser origin, and owned apps are trusted network peers. Apps serving untrusted executable content need a separate origin/boundary. Ordinary apps receive neither Docker nor OCI credentials.

Apply the current shared CSS contract from `design/` and the decisions in `style.md`: the After hours theme. Bundle a pinned copy of shared assets in each consumer rather than fetching mutable styles at runtime.

## Give each app its own idle lifecycle

Add `sablier.enable: 'true'` and `sablier.group: ${COMPOSE_PROJECT_NAME:-athenaeum}-<service>` to the hosted Compose service. Never label the main `athenaeum` website, `edge`, or `sablier`. Never share a group between independent apps. Caddy's `SABLIER_GROUP_PREFIX` must match Compose's project prefix. Keep standalone images free of Athenaeum-specific lifecycle policy.

Use `deploy/sablier/sablier.yaml` as the common policy: Docker stop strategy, no destructive startup stop, and automatic adoption of externally started apps (also the self-heal for a Sablier 1.18.0 store race that can leave an idle app running; keep it on). Its `default-duration` is the shortest tier and reaches only adopted apps; the session an app actually gets comes from its route's tier, sliding from the last request. Compose deployment starts apps normally; they sleep after inactivity. HTTP polling renews the session; a background job or an idle open WebSocket does not. Identify workloads that must keep running before opting them in.

Preserve explicit Caddy `route` ordering: deny the private health endpoint before Sablier, run Sablier before prefix stripping and proxying. Only HTML GETs receive the dynamic loading page. POSTs, APIs and assets use the blocking strategy so their request bodies survive a cold start and HTML is not substituted for JSON or CSS. Keep real Docker readiness healthchecks; do not mark apps ready merely because their process started.

Reuse `deploy/sablier/themes/athenaeum.html`, including its Go template refresh value and the bundled `design/mark.svg`. Keep the loading page usable without app assets, scripts, external services or motion. Sablier's API belongs only on the private `control` network with Caddy; apps must not join that network or receive the Docker socket. Run only one Sablier lifecycle manager per Docker daemon: groups separate requests, not provider-wide discovery.

Verify each group independently: cold HTML navigation, cold POST/API forwarding, prefix/query preservation, hidden probes not waking apps, idle shutdown, a second app remaining asleep, and the main website remaining available. `status` must accept cleanly stopped registered apps but reject crashes/missing services; `verify` deliberately wakes apps and requires readiness afterward. See `docs/reference/sablier.md` for the policy and local smoke test.

### Place the app in a session tier

Sablier keeps only the apps in use resident, which is what lets a resource-hungry app (a JVM, a loaded model, a database sidecar) join the list on the 2 OCPU / 12 GB host. It relaxes memory and nothing else. Every route names a tier, and the tier sets how long the app stays awake after its last request:

| Tier | Awake resident memory | Session | Snippet |
|---|---|---|---|
| Lightweight | under 256 MB | 72h | `light` |
| Middleweight | 256 MB – 1 GB | 12h | `middle` |
| Heavyweight | 1 – 3 GB | 4h | `heavy` |
| Super heavyweight | over 3 GB | 1h | `superheavy` |

The durations are the `SABLIER_SESSION_<TIER>` defaults in `apps.caddy`; `tests/sablier.test.ts` and `docs/reference/sablier.md` carry the same durations, so change them together. Place an app in four steps:

1. **Measure the app awake**, not its framework's reputation: run its image, exercise a realistic session, and read `docker stats`. Set `mem_limit` with headroom above that figure (the light apps use `1g`); a container over its own limit is killed alone instead of starving the host.
2. **Read the existing load**: the tiers in `deploy/caddy/routes.caddy`, then on the host `athenaeumctl status` for what is awake and `docker stats --no-stream` for what it holds.
3. **Check the budget**, about 11 GB after the host's own 1 GB: the measured memory of every light and middle app, which long sessions and daily use keep awake, plus the two largest heavy or super-heavy apps, the new one included at the tier its measurement gives. If that fits, the app takes that tier.
4. **If it does not fit**, demote middle apps to `heavy`, largest first, so they stop counting as always awake. If the light apps plus the two largest others still exceed the budget, the host is full: retire an app before adding this one.

What a tier does not change, and what to check alongside it:

- The image occupies the 50 GB boot volume whether or not the app runs. Check the host report's root filesystem figure before adding a multi-gigabyte image.
- Wake-up must cover start time. Set the Docker healthcheck `start_period` to cover loading, and raise the blocking `timeout` in `apps.caddy` (1m today, shared by every tier) past it; otherwise POSTs and API calls during a cold start receive Sablier's problem document while the HTML loading page keeps waiting.
- CPU is not relaxed: two heavy apps in use together share two OCPUs, and a single-process server is bounded by one of them.

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

For stateful apps, read `docs/guides/5-backup-and-recovery.md` and implement a consistent snapshot and isolated restore. Use SQLite's backup API or the database's own export, never a copy of a live database file. Register uploads and required keys too.

The host code registers stateful SQLite apps in `ops/athenaeum_ops/common.py` (`APPS`): a config key for the app's signing-key path (with a `/etc/athenaeum/<slug>-session-secret` default), its public prefix and its required tables. Service lists, preflight, Compose validation (`<slug>_session` secret, `apps/<slug>/data` mount), snapshot manifests, restore validation, installer key/directory creation and the HTTPS verification probes all derive from that entry. Add a per-app restore-test probe to `runtime.py`, the key/directory names to `ops/local-stack`, `<SLUG>_IMAGE`/`<SLUG>_SECRET_FILE` to `deploy/production.env.example`, and cover the app in the tests (recovery fixtures, installer, local stack, runbook, browser check). Quacktuaries and Bernoulli are the two examples. An app that is not SQLite-backed needs its own hook, manifest entry and restore path. Do not bypass the existing fail-closed checks: merely creating a new app data directory is insufficient and stops the current backup.

## Verify and deliver

Check independent builder dry runs and both image lines. Verify standalone root-path behavior and a complete hosted workflow, including redirects, assets, cookies and persisted records across replacement. For stateful apps, prove that a backup restores in isolation. Report native ARM or live checks accurately; use synthetic data for local testing.

Source changes are committed/pushed on the workstation, then synced with `athenaeumctl repo sync`. Host-tool changes require `athenaeumctl self update`; Compose-only changes use `docker deploy`; published image changes use `docker pull --deploy`. Finish with `athenaeumctl verify`. For a new app, that order also creates its key and data directory before the first deployment; see "Add a registered app" in `docs/guides/3-daily-usage.md`. These deployment commands take a backup first; schedule them outside active classroom use. See `docs/guides/3-daily-usage.md`.

Keep incompatible schema changes explicit and preserve compatible image versions for recovery. Add unattended migrations or image-update automation only if requested. Update the architecture contract and relevant guide when behavior changes; keep operational documentation current, without phase histories or duplicated upgrade instructions. Hand off the public URL, state/secret paths, required settings, validation and actual deployment status.
