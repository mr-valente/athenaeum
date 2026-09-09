# Manual builds and deployment

Build on the Arch workstation, push to Docker Hub, then SSH to the VM and run Compose. Images change only when you update them. Hourly backups are the only scheduled Athenaeum job.

## Build and publish

Your shared Fish command reads `~/.config/builder/builds.yaml`. Its `athenaeum` entry uses `docker/compose.yaml` to build three separate ARM64 images from this checkout and the sibling `~/forge/quacktuaries` checkout. No changes to the shared build function are needed.

In your normal **local Fish shell**:

```fish
docker login --username valentemath
build --dry-run --version v0.1.0 athenaeum
build --version v0.1.0 athenaeum
```

Use the explicit version for the first release. After that, `build athenaeum` increments the patch version. `build --no-push --version v0.1.0 athenaeum` tests the build locally without publication or version-state changes.

| Service | Docker Hub moving image | Example retained version |
| --- | --- | --- |
| Athenaeum | `valentemath/athenaeum:latest` | `valentemath/athenaeum:v0.1.0` |
| Caddy edge | `valentemath/athenaeum:latest-edge` | `valentemath/athenaeum:v0.1.0-edge` |
| Quacktuaries | `valentemath/athenaeum:latest-quacktuaries` | `valentemath/athenaeum:v0.1.0-quacktuaries` |

All three images share a release version because one builder invocation publishes the stack. They remain separate containers; Compose only recreates changed services. Keep version tags and avoid overwriting past releases. Source commits and published image digests belong in your recovery notes.

Quacktuaries executes ARM Python during its build. ARM64 emulation has been registered on this workstation. After a reboot, or on another AMD64 machine, check that `/proc/sys/fs/binfmt_misc/qemu-aarch64` exists. If missing, run this in your **local terminal** before building:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

This registers ARM emulation with the host kernel for the current boot. The registration is needed for the default builder to execute ARM build steps. [Docker cross-platform build instructions](https://docs.docker.com/build/building/multi-platform/). `ops/local-stack` continues to build native development images with separate local tags.

The shared builder saves `versions.txt` only after every push succeeds. A partial push can leave the moving tags at different versions; finish the build/push before updating the VM. Review and test local source first: the build includes uncommitted files. Athenaeum runs `npm run verify` inside its image build; exercise Quacktuaries changes through the local stack.

## Start and update on the VM

Complete [Part 2](guides/2-oracle-setup-guide.md) for the disk, secrets, HTTPS, and backups. The root `compose.yaml` has Docker Hub image defaults and no build definitions. Public images need no VM registry credentials; private images require `sudo docker login` with read access.

In your **VM shell**, during a break in classroom use, the runbook scripts provide the usual path:

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/stack update
sudo ops/runbook/verify-site
```

`stack update` holds the existing recovery lock across backup, pull, and container replacement. It stops immediately if a stage fails. Use `stack start` for an empty first installation; it pulls and starts without a backup. `stack pull` only downloads images. Scripts are commented in `ops/runbook/`; Part 2 walks through them in order.

The direct **VM Bash** equivalents are:

```bash
cd /opt/athenaeum/stack
sudo athenaeumctl preflight
sudo athenaeumctl backup
sudo docker compose --env-file /etc/athenaeum/compose.env -f compose.yaml pull
sudo docker compose --env-file /etc/athenaeum/compose.env -f compose.yaml \
  up -d --no-build --pull never --wait --wait-timeout 120
sudo docker compose --env-file /etc/athenaeum/compose.env -f compose.yaml ps
```

For the first start, omit `backup` until the database exists, then follow the guide's backup/restore checkpoints. For updates, stop if any command fails. `pull` downloads without replacing containers; `up` recreates changed services while preserving bind mounts. No `down` is necessary. Check HTTPS and a classroom workflow afterward. [Compose pull](https://docs.docker.com/reference/cli/docker/compose/pull/), [Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

The direct Compose commands above do not hold the backup tool's lock. Avoid overlapping them with an in-progress backup or restore; `ops/runbook/stack update` handles that lock for you. There is no class-activity gate, unattended image updater, or automatic rollback. Schedule updates when students are finished.

Image updates do not require fetching source on the VM. If you change Compose or host tools, deliberately update the VM checkout, review the config diff, and rerun the installer for changed host tooling.

## Manual image rollback

Choose a retained version known to work with the current database. For example, edit `/etc/athenaeum/compose.env` to add:

```dotenv
QUACKTUARIES_IMAGE=valentemath/athenaeum:v0.1.0-quacktuaries
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
