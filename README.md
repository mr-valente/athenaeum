# Athenaeum

`valentemath.com`: a website built from Markdown, with independently deployed apps on an Oracle ARM VM.

```text
Caddy (HTTPS)
  ├─ /                → Athenaeum static website
  └─ /quacktuaries/   → Quacktuaries classroom app

Block volume → persistent data
Oracle bucket → encrypted backups
Local builds → Docker Hub → manual deployment over SSH
```

## Operator guides

1. [Oracle infrastructure](docs/guides/1-oracle-infrastructure.md) — provision or recover cloud resources.
2. [Host setup](docs/guides/2-host-setup.md) — prepare Ubuntu and start the stack.
3. [Daily usage](docs/guides/3-daily-usage.md) — Git, images, logs and health checks.
4. [Backup and recovery](docs/guides/4-backup-and-recovery.md) — recovery drills, offsite copies and disaster recovery.

Use guides 1 and 2 for a fresh installation or replacement host, consulting guide 4 when restoring data. The [documentation index](docs/README.md) includes development and architecture references.

## Daily operation

On the workstation, build the project you changed:

```fish
build athenaeum
# Or:
build quacktuaries
```

Each project has its own image repository and version counter. Athenaeum publishes its site and edge; Quacktuaries publishes standalone and Athenaeum variants together. See [image builds](docs/development/image-builds.md).

On the VM, from any directory:

```bash
athenaeumctl docker pull --deploy
athenaeumctl verify
```

`athenaeumctl` requests sudo automatically through your existing administrator policy. Deployment takes a verified backup before image pull and replacement. Use `repo sync` for Git-managed Compose changes and `self update` for installed host-tool changes. Images update only when you deploy; backups run hourly.

## Develop locally

```bash
ops/local-stack init
ops/local-stack up --build --wait
```

Open the configured local HTTPS address; the default is `https://localhost:8443/`. See [local stack](docs/development/local-stack.md) for certificate trust and saved ports.

Edit `content/` for pages, project links and classroom resources. [Authoring](docs/development/authoring.md) describes Markdown, drafts, math and downloads. [Site development](docs/development/site-development.md) covers the toolchain and tests.

To add an app, use the [app-creation skill](skills/athenaeum-app/SKILL.md), [architecture contracts](docs/reference/architecture.md), and [shared style](style.md). Each app remains independently buildable and deployable.
