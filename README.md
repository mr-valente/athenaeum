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
build quacktuaries
```

Your shared `~/.config/builder/builds.yaml` has independent entries: Athenaeum publishes its website and edge as `valentemath/athenaeum:latest` and `:latest-edge`; Quacktuaries publishes both `valentemath/quacktuaries:latest` (standalone) and `:latest-athenaeum` from its own checkout. Each project has its own version counter and retained version tags. Build only the project you changed.

The first build asks for a starting version; `--version v0.1.0` supplies it explicitly. One `build quacktuaries` publishes both variants at the same Quacktuaries version, following Tailgate's multiple-output pattern.

[Part 1: Oracle provisioning](<docs/guides/1 - athenaeum_oracle_gui_guide.md>) covers creating the VM and first SSH connection. Continue with **[Part 2: Ubuntu 26.04 host setup and deployment](docs/guides/2-oracle-setup-guide.md)**. Normal setup has two configuration files:

- `compose.env`: domain and contact email, with optional image-version overrides.
- `recovery.json`: disk UUID, public backup key, Oracle region/namespace/bucket.

The installer also generates a persistent session-secret file. After setup, SSH to the VM during a break in classroom use:

```bash
athenaeumctl docker pull --deploy
athenaeumctl verify
```

The `athenaeumctl` command center works from any directory and invokes sudo automatically through your existing administrator policy. Use `repo sync` for Git changes, `self update` for host tools, and `docker pull --deploy` for a verified backup followed by image deployment. `status`, `verify`, and `docker logs` cover routine checks. See the [daily command reference](docs/delivery.md#daily-commands-on-the-vm) and [upgrade steps for an existing VM](docs/guides/2-oracle-setup-guide.md#upgrade-an-existing-vm-to-the-command-center). The [commented runbook scripts](ops/runbook) handle first setup and recovery drills.

The [manual delivery guide](docs/delivery.md) covers publishing, first startup, failed updates, and version rollback. [Recovery](docs/recovery.md) covers restoring data. Backups run hourly; image updates are manual.

The root [Compose file](compose.yaml) defines production images and persistent data mounts. [docker/compose.yaml](docker/compose.yaml) supplies workstation release builds; `compose.local.yaml` supplies the local development stack. Disk guards, resource/log limits, encrypted backups, and persistent session keys remain in place.

## Continue building

- [Implementation plan](docs/implementation-plan.md): remaining work and acceptance.
- [Shared style](style.md): still to be selected; do not copy Quacktuaries' current theme.
- [Application skill](skills/athenaeum-app/SKILL.md): instructions for an agent adding an app.
- [Site development](docs/development.md): build tooling and tests.

> Create [APP] at `valentemath.com/[SLUG]/`. Read `skills/athenaeum-app/SKILL.md` in the Athenaeum checkout and its `style.md`, then build the app and integrate it.

The operator has completed the Oracle runbook and verified the live website and recovery checkpoints. Cloud Run data migration remains a separate operation.
