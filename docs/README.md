# Documentation

Athenaeum is a Markdown website and independently built apps on one Oracle ARM VM. Use the guides to operate it or reconstruct it; use the references when changing its design or recovery behavior.

## Operator guides

1. [Oracle infrastructure](guides/1-oracle-infrastructure.md) — provision or recover the VM, network, volume, bucket, and instance permissions.
2. [Host setup](guides/2-host-setup.md) — configure Ubuntu, install host tools and start a prepared stack.
3. [Daily usage](guides/3-daily-usage.md) — Git sync, image deployments, logs and verification.
4. [Writing and arranging content](guides/4-authoring.md) — start with a home-page edit, organize sections and lessons, add app links, and preview and publish Markdown.
5. [Backup and recovery](guides/5-backup-and-recovery.md) — recovery drills, retention, offline validation and disaster recovery.

Guides 1 and 2 describe a fresh installation or a replacement after a disaster. For an independent duplicate, choose its own domain, OCI resources, data volume, bucket and recovery identity. A replacement reuses surviving resources and restores the original application data and signing key.

## Development

- [Site development](development/site-development.md) — pinned Node toolchain, source layout, and tests.
- [Local stack](development/local-stack.md) — HTTPS and classroom workflow testing with disposable local data.
- [Image builds](development/image-builds.md) — independent Fish builder entries, Docker Hub tags, and release checks.
- [Sablier lost-expiry bug](development/sablier-bug-report.md) — upstream bug report to file, and the checklist for retiring the workaround after the fix.
- [App-creation skill](../skills/athenaeum-app/SKILL.md) — integrate another independently deployable application.
- [Shared style](../style.md) — visual direction and decisions; [CSS contract](../design/README.md) describes the current implementation.

## Reference

- [Architecture and application contracts](reference/architecture.md) — services, networking, files, and integration requirements.
- [On-demand applications](reference/sablier.md) — separate Sablier groups, 12-hour idle sessions, loading theme and verification.
