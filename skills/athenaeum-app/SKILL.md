---
name: athenaeum-app
description: Build or integrate an application for the Athenaeum ecosystem, including its container, URL prefix, shared style, persistence, and Markdown project link.
---

# Athenaeum apps

Locate the Athenaeum checkout from the prompt or sibling `athenaeum/`. Read its `README.md`, `style.md`, and current Compose/Caddy files. Preserve unrelated changes. This skill does not authorize publication, cloud changes, or migration by itself.

Build one independently deployable container using the framework that fits the app. Keep the integration small:

- Add a service to `compose.yaml` on the private `apps` network, with no host-published port. Add a local build entry to `compose.local.yaml` if useful. Use Docker service names; do not assign static IPs. Owned apps are trusted network peers, not isolated tenants.
- Add Caddy's `/<slug>` slash redirect and `handle_path /<slug>/*` route. Caddy strips the prefix; the app must generate prefixed links, forms, redirects, assets, and API URLs. Reserve the prefix in `deploy/reserved-paths.json`.
- Add the project's Markdown page under `content/projects/`. This is the site's only editorial project catalog.
- Apply shared CSS from `style.md`. Until selected, use accessible provisional UI; do not copy Quacktuaries' current theme. Record the shared style version when one is adopted.
- Give browser cookies unique names, the app's path, and appropriate production security flags. Same-domain paths share an origin. Apps serving untrusted executable content need a separate origin.
- Put persistent data under `/srv/athenaeum/apps/<slug>/`, mounted at `/data` or the app's documented equivalent. Keep non-root ownership, missing-disk protection, health checks, resource/log limits, and graceful shutdown. Store secrets outside Git/images. Ordinary apps get neither Docker nor OCI credentials.

For a stateless app, document that Git/image rebuilding is its recovery method. For stateful apps, read `docs/recovery.md` and add a consistent backup and isolated restore procedure. Use SQLite's backup API or the database's own export, never copy a live database file. Register uploads and required keys too.

For image delivery, follow `docs/delivery.md`: build locally for the VM's architecture, push to Docker Hub, and update manually with Compose over SSH. Add the image build to `docker/compose.yaml` and the shared Fish builder configuration as appropriate. Keep version tags for recovery. Plan incompatible database changes explicitly; do not add unattended migrations or image-update automation without a request.

The current backup tooling knows Quacktuaries. A new stateful app needs its own consistent backup and restore support. Keep updates manual and scheduled outside active use.

Verify the public prefix with a complete user workflow, including redirects/assets/cookies. For stateful apps, verify container replacement preserves records and a backup restores in isolation. Test native ARM when available and report pending checks honestly. Keep the existing deployment/data intact until an authorized migration is accepted.

Hand off the URL, data/secret paths, required configuration, checks, and deployment status. Keep deeper operator instructions in the existing delivery/recovery docs rather than repeating them here.
