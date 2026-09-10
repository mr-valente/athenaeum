# Manual builds and deployment

Build on the Arch workstation, push to Docker Hub, then SSH to the VM and run Compose. Images change only when you update them. Hourly backups are the only scheduled Athenaeum job.

## Build and publish

Your shared Fish command reads `~/.config/builder/builds.yaml`. Each application has its own build entry, source checkout, image repository and version counter. Athenaeum's `docker/compose.yaml` builds its site and edge only. Quacktuaries' own `docker/compose.yaml` builds both its standalone and Athenaeum variants.

In your normal **local Fish shell**, for the first releases:

```fish
docker login --username valentemath
build --dry-run --version v0.1.0 athenaeum
build --version v0.1.0 athenaeum
build --dry-run --version v0.1.0 quacktuaries
build --version v0.1.0 quacktuaries
```

Later, build only the project you changed:

```fish
build athenaeum
build quacktuaries
```

Each command increments its own project's patch version. Add `--no-push --version v0.1.0` to test a build without publishing or changing version state. The two version numbers need not match.

| Image | Moving reference | Example retained version |
| --- | --- | --- |
| Athenaeum website | `valentemath/athenaeum:latest` | `valentemath/athenaeum:v0.1.0` |
| Caddy edge | `valentemath/athenaeum:latest-edge` | `valentemath/athenaeum:v0.1.0-edge` |
| Quacktuaries standalone | `valentemath/quacktuaries:latest` | `valentemath/quacktuaries:v0.1.0` |
| Quacktuaries on Athenaeum | `valentemath/quacktuaries:latest-athenaeum` | `valentemath/quacktuaries:v0.1.0-athenaeum` |

One `build quacktuaries` publishes both variants, following Tailgate's separate `outputs` entries. They share **Quacktuaries'** version counter; Athenaeum's version is independent. The standalone image serves at the root path by default. The Athenaeum target defaults to production mode and `/quacktuaries`; secrets and proxy trust are supplied at runtime. Neither image needs the Athenaeum source checkout to build or run.

`--rebuild` reuses the project's recorded version when retrying an interrupted publication. Retain version tags and avoid overwriting previous releases with changed code.

Release recipes default to ARM64 for Oracle. Quacktuaries can target another host with `QUACKTUARIES_PLATFORM=linux/amd64 build quacktuaries`; these builds publish a single architecture, so choose it deliberately rather than overwriting a tag needed by another host. Native development builds remain in each repository's development Compose file.

Quacktuaries executes ARM Python during its build. If `/proc/sys/fs/binfmt_misc/qemu-aarch64` is missing on this AMD64 workstation, register emulation for the current boot:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

See [Docker cross-platform builds](https://docs.docker.com/build/building/multi-platform/). The shared builder saves that project's `versions.txt` record only after all its pushes succeed. Finish publication before updating the VM. Build inputs include uncommitted files; test first and save source commits plus published image digests in recovery notes.

If moving from the old grouped image, update the VM's production `compose.yaml` so Quacktuaries pulls its own repository. Review `/etc/athenaeum/compose.env` for an old `QUACKTUARIES_IMAGE` override and update/remove it deliberately. Keep prior images for existing backups until their replacements are verified; image naming changes do not migrate or replace app data.

## Daily commands on the VM

After installing the current tools, `athenaeumctl` works from any directory in your SSH session. Its launcher invokes sudo automatically using your existing administrator policy; a password prompt may still appear. No extra sudoers rule or Docker-group membership is needed. Run `athenaeumctl --help` or a subcommand's `--help` for options.

| Command | What it does |
| --- | --- |
| `athenaeumctl status` | Container health, disk space and backup freshness; nonzero if unhealthy/stale |
| `athenaeumctl verify` | Public HTTPS routes, redirects, container health and metadata isolation |
| `athenaeumctl docker images` | Show configured image references, including `/etc` overrides |
| `athenaeumctl docker ps` | Show containers and health |
| `athenaeumctl docker logs quacktuaries --tail 100 -f` | Follow app logs; Ctrl-C stops following |
| `athenaeumctl docker pull` | Download configured images without replacing containers |
| `athenaeumctl docker pull --deploy` | Verified backup, image pull, then Compose replacement and health wait |
| `athenaeumctl docker deploy` | Verified backup, then apply Compose with already downloaded images |
| `athenaeumctl repo status` | Show the VM checkout's branch, commit and local edits |
| `athenaeumctl repo sync` | Fetch GitHub main, validate candidate Compose, fast-forward a clean checkout |
| `athenaeumctl self update` | Install host tools from the synced checkout without restarting Docker |
| `athenaeumctl backup` | Create and verify an encrypted backup now |
| `athenaeumctl list` | List verified snapshots and bucket usage |
| `athenaeumctl preflight` | Check storage, configuration and session key |

`export`, `restore`, and `restore-test` retain their existing flags; see [recovery](recovery.md). The original `ops/runbook/` scripts and systemd command paths remain available. Use the runbook's `stack start` only for an empty first installation; deployed sites use the backup-protected deployment commands above.

### Publish image changes

Build only the changed project on your workstation. After its Docker Hub pushes finish, run on the VM during a break in classroom use:

```bash
athenaeumctl docker pull --deploy
athenaeumctl verify
```

The deployment holds the existing host lock across backup, pull and replacement. A failed backup stops before any pull or replacement; a failed pull stops before replacement. Health failure is reported without automatic rollback. Choose a quiet time: there is no class-activity gate or unattended updater. `docker logs -f` does not hold the backup lock.

### Edit Compose or host scripts through Git

Yes: edit this repository on your workstation, commit and push, then sync it from the VM. Keep public configuration such as `compose.yaml` in Git. Keep VM settings and image overrides in `/etc/athenaeum/compose.env`, backup settings in `/etc/athenaeum/recovery.json`, the signing key in its existing secret file, and live data under `/srv/athenaeum`. Those paths are outside the checkout.

For a Compose-only change, after your workstation Git push:

```bash
athenaeumctl repo sync
athenaeumctl docker deploy
athenaeumctl verify
```

If the new Compose references images that are not already downloaded, use `docker pull --deploy` instead. Website content, the baked Caddyfile, and application code require rebuilding/publishing the relevant image; fetching source alone does not update those running images.

`repo sync` shows a change summary and validates the fetched commit's Compose mounts and signing-key path in a temporary worktree before fast-forwarding the VM checkout. It uses the existing checkout owner's Git identity and credentials. It refuses local edits/untracked files, a different origin/branch, and ahead/diverged history. It never resets, stashes, merges divergent history or deploys containers. Keep VM-specific overrides outside Git so ordinary sync stays clean. Use `git -C /opt/athenaeum/stack diff` as the checkout owner to inspect local tracked edits when needed.

If `ops/` changed, refresh the installed command tools after syncing:

```bash
athenaeumctl self update
```

This uses `ops/install-host` directly, preserving the existing key, settings and data. It does not run the first-time service activation script or restart Docker. Changes that explicitly require restarting a host service still need their own maintenance step. The currently installed command code remains in `/opt/athenaeum/operations`; repository sync alone does not replace it.

For an existing scp-based VM, complete the [one-time command-center upgrade](guides/2-oracle-setup-guide.md#upgrade-an-existing-vm-to-the-command-center) first.

## Manual image rollback

Choose a retained version known to work with the current database. For example, edit `/etc/athenaeum/compose.env` to add:

```dotenv
QUACKTUARIES_IMAGE=valentemath/quacktuaries:v0.1.0-athenaeum
```

Then run on the VM:

```bash
cd /opt/athenaeum/stack
sudo docker compose --env-file /etc/athenaeum/compose.env -f compose.yaml pull quacktuaries
sudo docker compose --env-file /etc/athenaeum/compose.env -f compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 120 quacktuaries
```

Use `ATHENAEUM_IMAGE` or `EDGE_IMAGE` for the other services. Remove the override when ready to follow its moving tag again. A digest reference such as `valentemath/athenaeum@sha256:...` can pin exact bytes.

Image rollback leaves the database unchanged. A schema-incompatible update needs a planned recovery operation, including preservation of newer data, rather than simply choosing an older image. See [recovery](recovery.md).

## If the old automation was already installed

Skip this section on a fresh VM. Before using the manual workflow on an existing installation:

1. Disable the former updater timer and stop its service during maintenance. Check that an old build/update is no longer running before proceeding:

   ```bash
   sudo systemctl disable --now athenaeum-updates.timer
   sudo systemctl stop athenaeum-updates.service
   ```

2. Remove the deployment key from the dedicated `athenaeum-deploy` account's `authorized_keys` and its sudoers rule. Remove the old deployment/publication secrets and variables from GitHub Actions in any repositories where you configured them; disable any remaining sibling deployment workflow. Keep your administrator SSH access.
3. Update the checkout and reinstall the host tools. The installer installs backup/recovery tools only. Old files outside the checkout are not automatically deleted; after preserving them, remove the obsolete updater units and `/usr/local/sbin/athenaeum-trigger`, then run `sudo systemctl daemon-reload`.
4. Preserve the old `/srv/athenaeum/deploy-state` and retained images until the manual stack and a new backup are verified. The new tools use only the configured Compose files; they no longer load the old managed image overlay.

The backup timer, data directories, and persistent session key remain in use. See Part 2 for the manual pull/start and recovery checks.
