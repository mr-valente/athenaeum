# Athenaeum

`valentemath.com`: a website built from Markdown, with your apps alongside it on an Oracle ARM VM.

```text
Caddy (HTTPS)
  ├─ /                → Athenaeum (static website)
  └─ /quacktuaries/    → Quacktuaries (classroom app)

Block volume → live data
Oracle bucket → encrypted backups
GitHub push → Docker Hub → automatic update
```

## Start locally

```bash
ops/local-stack init
ops/local-stack up --build --wait
```

Open `https://localhost:8443/`. The local certificate needs browser trust; see [local development](docs/local-ecosystem.md). Existing local settings, ports, data, and keys are preserved.

Edit `content/` to publish pages, projects, and classroom resources. Navigation comes from the Markdown directory. See [authoring](docs/authoring.md).

## Set up the VM

[Part 1: Oracle provisioning](<docs/guides/1 - athenaeum_oracle_gui_guide.md>) covers creating the VM and first SSH connection. Continue with **[Part 2: Ubuntu 26.04 host setup and deployment](docs/guides/2-oracle-setup-guide.md)**. Normal setup has **two configuration files**:

- `compose.env`: Docker Hub username, domain, contact email.
- `recovery.json`: disk UUID, public backup key, Oracle region/namespace/bucket.

There is also a generated session-secret file. It is persistent private data, not another settings document to maintain.

After setup, editing Markdown or an app and pushing to GitHub is enough. CI tests both architectures and publishes images; the VM checks every 15 minutes. Quacktuaries waits until all classes/lobbies have ended and takes a verified backup before updating. Caddy updates are manual.

## Everyday operations

```bash
sudo athenaeumctl status
sudo athenaeumctl backup
sudo athenaeumctl pause-updates
sudo athenaeumctl resume-updates
sudo athenaeumctl rollback quacktuaries
```

[Delivery](docs/delivery.md) covers updates and failures. [Recovery](docs/recovery.md) covers restoring data. The main [Compose file](compose.yaml) defines all three services; `compose.local.yaml` only supplies local builds, ports, and TLS.

The simplification removes static container IPs, per-app subnet planning, CPU quotas, startup dependencies, GitHub release assets, a separate update allowlist, and custom maintenance-window configuration. Docker resolves service names, image metadata travels with the image, and systemd owns scheduling. App containers share one private network: it is appropriate for our trusted apps, not an isolation boundary between hostile tenants.

Disk guards, resource/log limits, encrypted backups, class deferral, and compatible image rollback remain. These protect actual data and classroom use. Schema-changing releases require a deliberate maintenance task.

## Continue building

- [Implementation plan](docs/implementation-plan.md): remaining work and acceptance.
- [Shared style](style.md): still to be selected; do not copy Quacktuaries' current theme.
- [Application skill](skills/athenaeum-app/SKILL.md): instructions for an agent adding an app.
- [Site development](docs/development.md): build tooling and tests.

> Create [APP] at `valentemath.com/[SLUG]/`. Read `skills/athenaeum-app/SKILL.md` in the Athenaeum checkout and its `style.md`, then build the app and integrate it.

Status: the VM is provisioned and SSH works, as reported by the operator. Application tooling is implemented and tested locally. Image publication, live ARM application/backup verification, stack deployment, and Cloud Run migration are not yet verified here.
