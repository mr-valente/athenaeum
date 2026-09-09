# Local ecosystem

Run from the Athenaeum checkout, with the Quacktuaries checkout beside it. Requires Docker Engine, Compose 2.24.4+ (for the local port override), and Python 3.

```bash
ops/local-stack init
ops/local-stack up --build --wait
ops/local-stack ps
```

Open `https://localhost:8443/`. Caddy creates a local CA; accept its certificate in your test browser or import only `.state/local/edge/data/caddy/pki/authorities/local/root.crt`. Do not share the neighboring private key. Nothing is added to the system trust store automatically.

```bash
curl --cacert .state/local/edge/data/caddy/pki/authorities/local/root.crt \
  https://localhost:8443/
```

Initialization preserves existing data, keys, and settings. Check `.state/local/compose.env` for your saved ports; this workstation's existing setup uses **4384/4385**. No subnet configuration is needed. Old `QUACK_*` network settings, if present in an existing local file, are ignored.

## Work and test

```bash
ops/local-stack up --build --wait           # Rebuild changed content/app
ops/local-stack logs --tail 50
ops/local-stack config --quiet
ops/local-stack up --force-recreate --wait quacktuaries
ops/local-stack down                        # Stop; retain all data
```

Use synthetic teacher/student names: create a game, join as a student in another browser profile, start, inspect/sell, end, and export. Teacher ownership and student rejoin tokens rely on both the database and session key. CSV game exports are not full database backups.

The local wrapper uses a separate Compose project, loopback ports, your UID/GID, and `.state/local/` bind mounts. It does not change cloud resources or public DNS. Builds may download dependencies/images.

## The stack

| Service | Internal ports | Data | Memory limit |
| --- | --- | --- | --- |
| Caddy `edge` | 8080/8443; health 8081 | Certificate data/config | 256 MiB |
| `athenaeum` | 8080 | None | 256 MiB |
| `quacktuaries` | 8000 | Database at `/data`; temporary scratch | 1 GiB |

All services use non-root processes, read-only root filesystems, restricted capabilities, bounded logs, and health checks. Only Caddy publishes host ports. Apps share one private `apps` network; only Caddy also joins the outbound `default` network. Docker supplies service names and addresses. This intentionally treats our apps as trusted peers. Quacktuaries trusts proxy headers on that unpublished internal port; Caddy sets them from the public request. A new untrusted app would need a separate boundary.

Caddy redirects `/quacktuaries` to `/quacktuaries/`, preserves queries, and strips the prefix upstream. App links, forms, assets, and redirects include the public prefix. Cookies have a unique name, app path, and production security flags. All apps on the domain still share one browser origin.

Bind mounts require existing directories. Production also uses the filesystem UUID guard; a directory's existence alone does not prove that the block disk mounted. See [recovery](recovery.md).

## Automated checks

```bash
ops/local-stack up --build --wait
ops/local-stack ps
```

This builds native development images and waits for container health checks. Run host recovery tests as described in [recovery](recovery.md), and use the browser workflow below to verify routing and classroom behavior. Production ARM64 builds and Docker Hub publication are described in [manual delivery](delivery.md).

Browser checks use `tests/browser/` and create synthetic data only:

```bash
npm ci --prefix tests/browser
npm --prefix tests/browser exec -- playwright install chromium
TEST_BASE_URL=https://localhost:4385 node tests/browser/ecosystem.mjs
ops/local-stack up --force-recreate --wait quacktuaries edge
TEST_BASE_URL=https://localhost:4385 node tests/browser/ecosystem.mjs --verify-recreated
```

Use your configured HTTPS port. The runner forces connections to loopback and verifies the generated local CA. It checks teacher/student flows, cookies, exports, static files, and persistence across replacement. Local screenshots are not final style acceptance.

Production setup uses the two example configuration files and [Oracle guide](guides/2-oracle-setup-guide.md). Cloud HTTPS, native ARM, mount-loss/reboot recovery, and Cloud Run migration remain deployment checks. The original subdomain and its data have not been changed.
