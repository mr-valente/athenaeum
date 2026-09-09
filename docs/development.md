# Local development

Phases A/B provide directory-driven Markdown publishing and a stateless production container. Phase C adds the [local HTTPS ecosystem](local-ecosystem.md). For editorial work, see [writing content](authoring.md).

## Pinned toolchain

| Tool | Version |
| --- | --- |
| Node.js | 24.20.0 LTS |
| npm | 11.19.0, bundled with this Node release |
| Astro | 7.3.1 |
| Astro Markdown renderer | 7.3.0, explicitly satisfying Astro's peer dependency |
| Astro checker | 0.9.10 |
| TypeScript | 6.0.3, within the checker's supported peer range |

Versions were resolved from the official Node distribution and npm registry on 8 September 2026. `.node-version` identifies the runtime; `package.json` and `.npmrc` enforce the exact Node/npm versions. Direct dependencies use exact versions; `package-lock.json` fixes transitive dependencies and integrity hashes. The newer TypeScript major was intentionally not selected because the pinned checker does not support it.

Install [Node 24.20.0](https://nodejs.org/dist/v24.20.0/) using your preferred version manager or the official archive for your operating system and CPU. Verify an archive against that release's `SHASUMS256.txt`. No global Astro installation is needed. See [Astro setup](https://docs.astro.build/en/install-and-setup/) for upstream prerequisites.

From the repository directory:

```bash
node --version
npm --version
npm ci
npm run verify
npm run dev
```

Open the local URL printed by Astro, normally `http://127.0.0.1:4321/`. Stop the server with Ctrl-C. To serve the generated build locally, run `npm run preview` after building. Both commands bind to loopback. For a different port, use `npm run dev -- --port 4322`.

In an agent environment, Astro may automatically start dev/preview in the background; `--background` also enables this explicitly. Stop those servers with `npm exec -- astro dev stop` or `npm exec -- astro preview stop`. Replace `stop` with `status` or `logs` to inspect them.

`npm run check` runs Astro/TypeScript diagnostics. `npm run build` writes static output to `dist/`. `npm test` runs content validation tests and isolated real-build regressions. `npm run verify` runs tests, diagnostics, and the production build. `npm ci` installs the lockfile without upgrading it; do not substitute an unreviewed dependency update. Set `ASTRO_TELEMETRY_DISABLED=1` in your shell if you want to disable Astro telemetry.

## What to edit

- `content/`: all page text, project metadata, and referenced downloads. The [authoring guide](authoring.md) defines the supported format.
- `src/lib/content.ts`: metadata validation, routes, drafts, Markdown rendering, and link/asset resolution.
- `src/content.config.ts`: Astro content collection and development watcher.
- `src/lib/assets.ts`: reference-only asset copying and development serving.
- `src/pages/` and `src/layouts/`: static templates, sitemap, and 404 response.
- `design/tokens.css` and `design/base.css`: provisional shared CSS. [style.md](../style.md) remains unselected.
- `deploy/reserved-paths.json`: application prefixes that Markdown cannot shadow.
- `docker/caddy.json`: internal static server; the public edge is a separate service.

Do not put private class records or secrets in `content/`. `docs/`, `skills/`, and `style.md` are not copied into the generated site. There is no client JavaScript in the current production pages. Dev-only Astro tooling does not ship in the production image.

## Production container

Docker Engine with Buildx is sufficient; Node is installed only inside the build stage:

```bash
docker build -t athenaeum:local .
docker run --rm --name athenaeum-local \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 256m --cpus 1 \
  -p 127.0.0.1:8080:8080 athenaeum:local
```

Open `http://127.0.0.1:8080/`. Stop with Ctrl-C. In another terminal, `curl -fsS http://127.0.0.1:8080/_health` must return `ok`. The limits above are a local smoke-test envelope, not a capacity guarantee.

The runtime contains Alpine, Caddy, its configuration, and generated public assets. It runs as UID/GID `10001:10001`, requires no writable path or volume, and listens on HTTP 8080. There is no Node, package installation, source Markdown, admin endpoint, or automatic TLS at runtime. Phase C supplies the separate Caddy edge; see the local ecosystem guide.

Caddy uses JSON here to set [`apps.tls.disable_storage_clean`](https://caddyserver.com/docs/json/apps/tls#disable_storage_clean). This prevents unused TLS housekeeping from writing files in a read-only static server. That upstream option is experimental; recheck it when updating Caddy. The Dockerfile copies the target Caddy binary without its upstream low-port file capability, which is unnecessary on 8080 and prevents execution when all capabilities are dropped.

Base images are pinned by manifest digest. Static assets and tests run on the builder's native architecture; the final image receives the target architecture's Caddy binary and Alpine base. Cross-building does not execute ARM code.

To create both architectures without publishing:

```bash
docker buildx create --name athenaeum-builder --driver docker-container
docker buildx build --builder athenaeum-builder \
  --platform linux/amd64,linux/arm64 --tag athenaeum:local \
  --output type=oci,dest=/tmp/athenaeum.oci.tar .
docker buildx rm athenaeum-builder
```

An OCI archive is a local delivery artifact. To run one architecture locally, use `--platform linux/amd64 --load` (or `linux/arm64` on ARM) instead of `--output`. The multi-platform archive is not universally loadable with `docker load`; use a compatible image store or a registry. Production releases use the shared Fish builder and Docker Hub as described in [manual delivery](delivery.md).

## Dependency updates

Change the runtime pins together, then update exact dependency versions only within compatible peer ranges. Regenerate and review the lockfile; run a clean install, diagnostics, and production build. Before producing images, verify native ARM dependencies and repeat the checks on the selected image runtime. An x86 local build alone does not verify ARM execution.

## Phase B verification

Verified locally on 8 September 2026:

- All **27 tests passed**, including real Astro rebuilds that remove drafted/deleted pages and stale assets. Empty content, generated sections, filename titles, ordering, anchors, math, reserved paths, invalid metadata, traversal, symlinks, and draft exclusions are covered.
- Astro diagnostics: zero errors, warnings, or hints. The production image build runs `npm run verify` before packaging.
- AMD64 and ARM64 images exported together; manifest platforms and image architecture fields checked. Native **AMD64** runtime verified; native **ARM64** execution remains required before deployment.
- Caddy ran healthy as non-root with a read-only root filesystem, all capabilities dropped, no new privileges, and no volume. Deep routes returned 200, unknown and draft routes/assets returned 404, and directory redirects preserved query strings.
- Chromium checked keyboard skip-link access, desktop and 375px layouts, local KaTeX font loading and MathML, CSV/PDF downloads, and actual PNG decoding. The sample lesson's Letter PDF was visually inspected and fits on one page. No external requests or failed requests occurred in the bundled-content browser checks.
- An isolated development server refreshed page additions, edits, drafts, deletions, navigation, and referenced assets without a restart.
- With the starter content: AMD64 image size about **58 MB**, observed idle container memory **15.4 MiB**, and cold homepage transfer about **10.2 KB** in Chromium over local HTTP. These are development-machine observations, not Oracle measurements or classroom load tests. The static server initialized in under one second in this smoke test.
- Existing Oracle setup documents/PDFs, hosting research, the application skill, `style.md`, initial content, and token definitions were preserved. Quacktuaries was not modified.

Browser and print artifacts were produced in a temporary test directory, with the sample draft made visible only in a separate source copy. No global Node installation or host browser-library changes were needed. Final visual acceptance remains pending until the shared style is selected.

**Phase B delivery status:** source and local images only; no publication or cloud deployment. Phase C subsequently added the tested local Compose stack; see [local ecosystem](local-ecosystem.md).
