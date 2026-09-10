# 3 — Daily usage

Run `athenaeumctl` from any directory in your VM SSH session. It requests sudo automatically using your administrator policy; a password prompt may appear. Use `--help` on any command. Stop at a failed command and resolve the reported cause before continuing.

## Check the site

```bash
athenaeumctl status
athenaeumctl verify
athenaeumctl docker logs quacktuaries --tail 100 -f
athenaeumctl docker logs bernoulli --tail 100
```

`status` summarizes containers, disk space and backup freshness; the same facts reach the homelab through the [host report](#host-report) without logging in. `verify` checks HTTPS, redirects, health and metadata isolation. Ctrl-C stops log following. Use `docker ps` for container details or `docker images` for configured image references.

## Publish and deploy images

On your workstation, build only the project you changed:

```fish
build athenaeum
# Or:
build quacktuaries
build bernoulli
```

Each project has its own version; the classroom apps publish both standalone and hosted variants. See [image builds](../development/image-builds.md) for first versions and build checks.

After the pushes succeed, run on the VM during a break in classroom use:

```bash
athenaeumctl docker pull --deploy
athenaeumctl verify
```

This takes a verified backup, pulls images, recreates changed containers and waits for health. A failed backup or pull stops before replacement. `docker pull` alone only downloads images. Existing data and the signing keys remain in place.

## Change Compose or host tools

Edit, commit and push this repository from your workstation. Then on the VM:

```bash
athenaeumctl repo sync
athenaeumctl docker deploy
athenaeumctl verify
```

`repo sync` validates and fast-forwards a clean `main` checkout. It refuses local edits, untracked files and diverged history. It does not change running containers. `docker deploy` backs up and applies Compose with already downloaded images; use `docker pull --deploy` when downloads are also needed.

When `ops/` changes, run `athenaeumctl self update` after sync. It refreshes installed tools without restarting Docker. Source content and baked Caddy configuration require rebuilding the relevant image; Git sync alone does not publish them.

## Add a registered app

A new stateful app arrives as a Compose service, an edge route baked into a new edge image, and host-tool support. Publish the app's hosted image and the new Athenaeum images first, then during a classroom break:

```bash
athenaeumctl repo sync
athenaeumctl self update
athenaeumctl docker pull --deploy
athenaeumctl verify
```

`self update` creates the app's empty data directory and persistent signing key under `/etc/athenaeum`; it never replaces an existing key. The backup taken by `docker pull --deploy` records the new app as key-only until its first start creates a database. `verify` then probes every registered prefix.

Keep domain/email and image overrides in `/etc/athenaeum/compose.env`, backup settings in `/etc/athenaeum/recovery.json`, and secrets/data outside Git. To inspect checkout changes, use `athenaeumctl repo status` and, as its owner, `git -C /opt/athenaeum/stack diff`.

## Host report

Every 15 minutes `athenaeum-report.timer` runs `athenaeumctl report`, which overwrites one small JSON object in the backup bucket (`monitor-host.json` by default) with what Oracle's own APIs cannot see: how full `/` and `/srv/athenaeum` are, when the last backup was verified and whether the last attempt failed, the date of the last restore drill, and the state of each container. It signs the upload with the instance principal the backups already use, opens no port and adds no IAM grant. An external monitor, such as [oci-monitor](https://github.com/mr-valente/oci-monitor) in the homelab, reads it back with a separate read-only identity and turns it into a dashboard and phone alerts.

```bash
athenaeumctl report --print
sudo systemctl list-timers athenaeum-report.timer --no-pager
sudo journalctl -u athenaeum-report.service -n 5
```

The report never waits for the operation lock, so it cannot delay a backup or a deployment; while one is running it reports the containers as busy instead of half-replaced. The backup inventory ignores the object, and retention never deletes it. Each publish is one Object Storage request, about 3,000 a month, which together with the hourly backup stays inside the 50,000 the Always Free tier includes; keep the timer's cadence in step with the monitor's `INTERVAL_HOST`.

To turn the report off, set `"monitor_object": null` in `/etc/athenaeum/recovery.json` and run `athenaeumctl self update`; the installer disables the timer to match. A different object name must also be configured on the monitor and in its IAM policy.

## Routine care

Apps sleep independently after 12 hours without requests; the main site stays up. `status` accepts cleanly stopped apps without waking them. `verify` deliberately wakes every app and renews its idle session. Use `athenaeumctl docker logs sablier` for lifecycle failures. See [on-demand applications](../reference/sablier.md) for policy/theme edits and their explicit Sablier recreation command.

Use `athenaeumctl backup` for an extra verified snapshot and `athenaeumctl list` to inspect recovery points. Monthly, check disk space, Oracle usage, Ubuntu updates, and repeat the [recovery drill](5-backup-and-recovery.md#recovery-drill-and-offsite-copy). Keep published image versions needed by backups. [Guide 5](5-backup-and-recovery.md) covers backup failures, rollback, restoration and cleanup of temporary recovery files.
