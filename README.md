# Athenaeum

`valentemath.com`: a website built from Markdown, with your apps alongside it on an Oracle ARM VM.

```text
Caddy (HTTPS)
  ├─ /                → Athenaeum (static website)
  └─ /quacktuaries/    → Quacktuaries (classroom app)

Block volume → live data
Oracle bucket → encrypted backups
Local Fish build → Docker Hub → manual Compose update over SSH
```

## Start locally

```bash
ops/local-stack init
ops/local-stack up --build --wait
```

Open `https://localhost:8443/`. The local certificate needs browser trust; see [local development](docs/local-ecosystem.md). Existing local settings, ports, data, and keys are preserved.

Edit `content/` to publish pages, projects, and classroom resources. Navigation comes from the Markdown directory. See [authoring](docs/authoring.md).

## Build and deploy

In your normal Fish shell on this workstation:

```fish
build athenaeum
```

Your shared `~/.config/builder/builds.yaml` entry builds the site, edge, and sibling Quacktuaries app for ARM64 and pushes them to `valentemath/athenaeum` on Docker Hub. It publishes separate moving and versioned tags for each image. The first build asks for a starting version; use `build --version v0.1.0 athenaeum` to supply it explicitly.

[Part 1: Oracle provisioning](<docs/guides/1 - athenaeum_oracle_gui_guide.md>) covers creating the VM and first SSH connection. Continue with **[Part 2: Ubuntu 26.04 host setup and deployment](docs/guides/2-oracle-setup-guide.md)**. Normal setup has two configuration files:

- `compose.env`: domain and contact email, with optional image-version overrides.
- `recovery.json`: disk UUID, public backup key, Oracle region/namespace/bucket.

The installer also generates a persistent session-secret file. After setup, SSH to the VM during a break in classroom use:

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/stack update
sudo ops/runbook/verify-site
```

The [commented runbook scripts](ops/runbook) handle setup, mount checks, configuration, manual updates, HTTPS checks, and recovery drills. `stack update` holds the backup lock across a verified backup, image pull, and container replacement; a failure stops the sequence.

The [manual delivery guide](docs/delivery.md) covers publishing, first startup, failed updates, and version rollback. [Recovery](docs/recovery.md) covers restoring data. Backups run hourly; image updates are manual.

The root [Compose file](compose.yaml) defines production images and persistent data mounts. [docker/compose.yaml](docker/compose.yaml) supplies workstation release builds; `compose.local.yaml` supplies the local development stack. Disk guards, resource/log limits, encrypted backups, and persistent session keys remain in place.

## Continue building

- [Implementation plan](docs/implementation-plan.md): remaining work and acceptance.
- [Shared style](style.md): still to be selected; do not copy Quacktuaries' current theme.
- [Application skill](skills/athenaeum-app/SKILL.md): instructions for an agent adding an app.
- [Site development](docs/development.md): build tooling and tests.

> Create [APP] at `valentemath.com/[SLUG]/`. Read `skills/athenaeum-app/SKILL.md` in the Athenaeum checkout and its `style.md`, then build the app and integrate it.

Status: local build and recovery tooling is implemented. Docker Hub publication, live ARM stack deployment, Oracle backup verification, and Cloud Run migration require their operator checkpoints.
