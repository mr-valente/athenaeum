# Image delivery

Push to GitHub → native AMD64/ARM64 tests → Docker Hub images → VM update.

There are no GitHub release assets or repository allowlists to configure. The image names in Compose are the host's allowlist. `ci/images.json` in each source repository lists the images that CI builds.

## One-time repository setup

1. Put Athenaeum and Quacktuaries in their own public GitHub repositories.
2. Create public Docker Hub repositories: `athenaeum`, `athenaeum-edge`, and `quacktuaries`.
3. In each GitHub repository, set Actions variables `DOCKERHUB_USERNAME` and `ENABLE_PUBLICATION=true`. Add secret `DOCKERHUB_TOKEN` with permission to push those images.
4. Push the reviewed source to `main`. Wait for both native jobs and the final publish job to pass. Change the workflow branch if your default branch differs.

PRs build and test without publishing credentials. On main, each native job pushes a unique candidate tag only after its checks. The final job joins both architectures, verifies the indexes, and advances `:latest` last. If rerunning a failed workflow, **rerun all jobs**, so the attempt-specific native tags agree.

No GitHub release permissions, VM credentials, or inbound deployment webhook are needed. The host trusts images published to your configured Docker Hub repositories; annotations are compatibility metadata, not a separate signature-verification system.

## First start

Complete the [Oracle guide](guides/2-oracle-setup-guide.md) first, including the two private configuration files, session key, installer, metadata guard, DNS, and mounted disk. Then, with empty new app data:

```bash
sudo athenaeumctl deploy --initial
sudo athenaeumctl backup
```

Run the isolated [restore test](recovery.md) before enabling automation:

```bash
sudo systemctl enable --now athenaeum-backup.timer athenaeum-updates.timer
sudo athenaeumctl status
```

Initial deployment refuses existing project containers or app records. Existing Cloud Run data needs a separate migration; do not bypass that check to cut over.

## Updates

The host checks every **15 minutes**, resolving `:latest` to a fixed digest before pulling. This limits registry traffic on the free plan. Two automatically updated images produce 48 scheduled index inspections in six hours, plus actual update traffic; rate limits still depend on other users/workloads sharing the address. Failures back off and keep current containers running. [Docker Hub limits](https://docs.docker.com/docker-hub/usage/pulls/).

Only the changed service is recreated. Every Quacktuaries update checks for active classes, drains requests, makes a verified encrypted backup, then checks the replacement's health and HTTPS route. Any lobby/non-ended session defers updates; start and end an abandoned lobby through the existing teacher controls when appropriate.

```bash
sudo athenaeumctl deploy                 # Check now
sudo athenaeumctl deploy --app edge      # Deliberate Caddy update
sudo athenaeumctl pause-updates          # Survives reboot
sudo athenaeumctl resume-updates         # Allows subsequent checks
sudo athenaeumctl rollback quacktuaries  # Previous compatible image
```

A recent check or registry failure may defer another check briefly. Pause does not interrupt an update already holding the operations lock. Rollback is allowed while paused, but still protects active classes.

For an overnight schedule, use `sudo systemctl edit athenaeum-updates.timer`:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 02:00:00 America/New_York
```

Then run `sudo systemctl daemon-reload` and `sudo systemctl restart athenaeum-updates.timer`. The timer is persistent: a missed run may execute after boot. Pause updates when you need an unconditional freeze.

## Failures and data changes

```bash
sudo athenaeumctl status
sudo athenaeumctl status --json    # Full diagnostic details
sudo journalctl -u athenaeum-updates.service -n 50
sudo systemctl --failed
```

A failed healthy-start check rolls back to the previous compatible image. The failed digest is held so the timer does not repeatedly retry it. Publish a corrected image to continue. Rollback never overwrites newer database writes with an old backup.

`ci/images.json` has one numeric `schema_version`, currently `0`. Ordinary updates must keep the data format compatible with that version. A different version is refused before replacement; schema/data-format changes need a reviewed maintenance and restore plan. Update the version for incompatible changes even if SQLite's table definitions are unchanged. Unexpected schema changes also stop automatic recovery for review.

If a process is interrupted after replacement begins, status records an unfinished update. Pause updates, inspect the failure, then use `rollback APP` for a compatible interrupted update. A failed first deployment has no previous image: preserve its data and diagnose it before restarting.

If the host process was forcibly killed during the pre-update backup, Quacktuaries may retain a temporary maintenance hold. Pause updates and ensure no backup/update process is running. Find the app container with `docker ps`, then run `sudo docker exec CONTAINER_ID python -m app.operations release`. Do this only after confirming replacement never began or the interrupted update was resolved.

The updater retains current and previous images, removing at most five specifically recorded superseded images per check. It never globally prunes Docker images or volumes.

## Local checks

`python3 ci/images.py build` builds and smoke-tests this repository's native images without publication. `check` additionally runs Athenaeum's host tests with real age encryption. CI uses native GitHub runners for each architecture.

The disposable `tests/delivery_integration.py` drill checks real HTTPS, independent updates, failed-health rollback, and encrypted pre-update backup using local image fixtures. Publication ordering and registry failures are tested separately. Local results do not establish Docker Hub publication, native ARM execution, or Oracle activation.

Implementation references: [image index annotations](https://docs.docker.com/reference/cli/docker/buildx/imagetools/create/), [registry inspection](https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/).

Simplification verification (8 September 2026): 44 host/publication tests passed with real age encryption, both repositories' native AMD64 build/smoke commands passed, and Quacktuaries' five real-server plus two publication tests passed. The disposable HTTPS update/backup/rollback drill passed and removed its containers. Workflow validation and the portable app-skill validator passed. Native ARM jobs, public registry publication, and Oracle activation remain pending.
