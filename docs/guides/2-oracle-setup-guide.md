# Athenaeum on Oracle — Part 2 runbook

Continue after [Part 1](<1 - athenaeum_oracle_gui_guide.md>): the Ubuntu 26.04 ARM VM exists and your administrator SSH connection works. This runbook mounts its data volume, installs Docker and recovery tools, starts the three containers, and verifies backups.

Build images on your Arch workstation with your existing Fish `build` command. The VM pulls from Docker Hub. You decide when to run an update; only backups are scheduled.

## Before you begin

Keep two terminals open: **your computer** and **the VM**. The scripts have executable shebangs, so you can invoke them from either Bash or Fish. Blocks labeled **Bash** use Bash syntax; run `bash` first if needed. The shared `build` function runs in your normal **Fish** shell.

Have these values from Part 1 ready:

| Value | Used for |
|---|---|
| Reserved VM IPv4 and administrator SSH key | Connection and file transfers |
| Data volume size and attachment/device information | Identifying the separate data disk |
| Oracle region and Object Storage namespace | Backup configuration |
| Private Standard bucket `athenaeum-backups` | Encrypted backups |
| Instance-specific dynamic group and both IAM policies | Reading bucket settings and managing backup objects |

Use your actual VM IP wherever a command says `REPLACE_VM_IP`. The examples use `~/.ssh/athenaeum-oracle`; substitute your existing key if different. Preserve the existing Cloud Run app, its required records, and `quacktuaries.valentemath.com`. This runbook starts a fresh Oracle database; it does not migrate those records.

## Script map

All scripts live in [`ops/runbook/`](../../ops/runbook). Open any script to read its comments, or run it with `--help`. They print their current step and exit nonzero on failure. Stop at a failed checkpoint, fix its cause, then rerun that step.

| Where | Script | Purpose |
|---|---|---|
| Your computer | [`backup-key`](../../ops/runbook/backup-key) | Create or reuse the age identity; print the public recipient |
| VM | [`check-host`](../../ops/runbook/check-host) | Read-only OS and disk inventory |
| VM | [`mount-data`](../../ops/runbook/mount-data) | Preview/mount an existing ext4 UUID; preserve fstab |
| VM | [`install-docker`](../../ops/runbook/install-docker) | Preview/install official Docker packages |
| VM | [`configure-host`](../../ops/runbook/configure-host) | Prompt for settings and create two private files |
| VM | [`install-services`](../../ops/runbook/install-services) | Preview/install recovery tools, activate guards, test bucket access |
| VM | [`stack`](../../ops/runbook/stack) | Pull, first start, or manual backup-and-update |
| VM | [`verify-site`](../../ops/runbook/verify-site) | Check HTTPS routes, health, and metadata isolation |
| VM | [`check-recovery`](../../ops/runbook/check-recovery) | Backup, isolated restore test, and encrypted export |
| Your computer | [`download-backup`](../../ops/runbook/download-backup) | Download that export and verify its checksum |
| VM | [`enable-backups`](../../ops/runbook/enable-backups) | Enable hourly backups after recovery has passed |

Host scripts reject other operating systems/architectures and require `sudo` where appropriate. The scripts do not format disks, change DNS or cloud resources, publish images, or reboot the machine. Commands for those deliberate steps appear below.

## 1. Put the scripts on the VM

**Your computer — Bash**, from the Athenaeum checkout:

```bash
cd /home/nicholas/forge/athenaeum
ssh -i "$HOME/.ssh/athenaeum-oracle" ubuntu@REPLACE_VM_IP
```

Confirm you reached the expected machine using your known SSH host key. Keep the VM's public Compose and host scripts in a Git checkout so later workstation commits can be synced over SSH.

**VM:**

```bash
sudo apt-get update
sudo apt-get install -y git
sudo install -d -o ubuntu -g ubuntu /opt/athenaeum
git clone https://github.com/mr-valente/athenaeum.git /opt/athenaeum/stack
```

If `stack` already exists, preserve it rather than replacing it. An existing scp-based deployment can use the [command-center upgrade](#upgrade-an-existing-vm-to-the-command-center) below. The VM needs no Quacktuaries source checkout. Private configuration, keys and data remain outside this Git checkout.

**VM:**

```bash
cd /opt/athenaeum/stack
ops/runbook/check-host
```

**Checkpoint:** Ubuntu 26.04, ARM (`aarch64`), Python 3.14, and the full block-device tree are visible. Compare the separate data volume with Part 1's Oracle attachment: size, attachment details, and device must agree.

## 2. Prepare the data filesystem

The script deliberately accepts an existing filesystem UUID. Identifying and formatting a new blank disk remains an explicit operator step.

**VM — Bash:** Set the device only after comparing it with Oracle:

```bash
ATHENAEUM_DEVICE=/dev/REPLACE_VERIFIED_DATA_DISK
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS "$ATHENAEUM_DEVICE"
sudo wipefs --no-act "$ATHENAEUM_DEVICE"
sudo blkid "$ATHENAEUM_DEVICE"
```

**Disk checkpoint:** It must be the separate data disk, never the disk containing `/`, `/boot`, or `/boot/efi`. A new blank disk has no filesystem signatures, partitions, or mountpoints. `blkid` can return nonzero without output for a blank disk. If a filesystem already exists, preserve it: use its ext4 UUID and skip formatting. Investigate any unexpected filesystem or records.

**Only for the verified new blank disk:**

```bash
sudo mkfs.ext4 -L athenaeum-data "$ATHENAEUM_DEVICE"
sudo blkid "$ATHENAEUM_DEVICE"
```

Copy the filesystem `UUID`, not `PARTUUID` or the volume OCID. Preview, then apply:

```bash
sudo ops/runbook/mount-data --uuid REPLACE_FILESYSTEM_UUID
sudo ops/runbook/mount-data --uuid REPLACE_FILESYSTEM_UUID --apply
```

The script verifies ext4, refuses a disk mounted elsewhere, refuses to hide existing files beneath the mountpoint, and rejects conflicting fstab entries. It saves and verifies a unique `/etc/fstab.before-athenaeum-*` copy before adding its one UUID-based mount rule. Matching existing rules are preserved.

**Checkpoint:** `PASS` reports the exact writable ext4 UUID at `/srv/athenaeum`. Reboot to prove persistence:

```bash
sudo reboot
```

Reconnect, then repeat the read-only check:

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/mount-data --uuid REPLACE_FILESYSTEM_UUID
```

The printed result must identify the same filesystem, already mounted. If missing, diagnose the attachment/fstab entry before continuing. Do not format again. [Oracle UUID mounting guidance](https://docs.oracle.com/en-us/iaas/Content/Block/References/fstaboptions.htm).

## 3. Install Docker

**VM:** Update Ubuntu first:

```bash
sudo apt update
sudo apt upgrade
```

If a reboot is required, reboot, reconnect, and check the mount again. Then:

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/install-docker
sudo ops/runbook/install-docker --apply
```

The preview checks for conflicting distro packages and repository definitions. Apply installs Docker's official Ubuntu `resolute`/ARM64 repository, Docker CE, Compose, and host prerequisites. It downloads the repository signing key over HTTPS, preserves a differing existing key for review, and prints package progress. It may start/restart Docker; use a maintenance window on an existing host. [Docker's Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/).

**Checkpoint:** The Docker greeting runs, Compose reports its version, and `DOCKER-USER` exists. Keep Docker's iptables backend and `live-restore` disabled. The installer does not remove conflicting packages, flush firewalls, or change Docker-group membership for you.

## 4. Create your recovery key

**Your computer — Bash:**

```bash
sudo pacman -Syu --needed age
cd /home/nicholas/forge/athenaeum
ops/runbook/backup-key
```

The first command performs an Arch system update and installs age. The script creates `~/.ssh/athenaeum-backup-identity.txt` privately, or validates and reuses the existing file. It prints only the public `age1...` recipient.

**Checkpoint:** Save the private identity file in your password manager, independently of backups. Keep the printed public recipient for the next step. The VM uses the public recipient for scheduled encryption; it needs the private identity only temporarily during the restore drill.

## 5. Configure and install the host tools

**VM:**

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/configure-host
```

Enter the prompted values:

| Prompt | Value |
|---|---|
| Site domain | `valentemath.com` |
| ACME contact email | Your email for certificate notices |
| Public age recipient | The `age1...` line from step 4 |
| Oracle region | Part 1's region identifier, such as `us-ashburn-1` |
| Object Storage namespace | Namespace from Part 1, not tenancy/compartment name |
| Backup bucket | `athenaeum-backups` |
| Backup bucket cap | Default 8,000,000,000 bytes; reduce for your remaining allowance |

The script discovers the UUID from the exact mounted path. It validates inputs and Compose before creating `/etc/athenaeum/compose.env` and `/etc/athenaeum/recovery.json`, both mode 0600. Existing complete settings are checked and preserved. Partial or invalid settings require deliberate editing; the script does not overwrite them.

**Checkpoint:** Both settings files validate. No private identity or registry token was requested. The storage cap is for encrypted object backups, not the live volume size.

Install the tools and activate the guards:

```bash
sudo ops/runbook/install-services
sudo ops/runbook/install-services --apply
```

The first invocation previews the existing installer and checks Python venv support. Apply installs `python3-venv` if needed, then:

1. Installs the isolated OCI SDK environment, checksum-pinned age tools, and `athenaeumctl`.
2. Creates owned data directories and a strong session-signing key for an empty installation. Existing keys and data are preserved; replaced tool files have rollback copies under `/var/lib/athenaeum/install-rollback-*`.
3. Activates the metadata guard and **restarts Docker** to apply its exact-UUID disk guard.
4. Checks storage, the firewall rule, and actual Oracle bucket read access using the instance principal.

The installer also places `athenaeumctl` on your normal PATH. It works from any directory and requests sudo automatically through your existing administrator policy. A password prompt may still appear; no new sudoers grant is installed.

**Checkpoint:** Guards are active and `list` can read the bucket. No applications or backup timer have been enabled by this step. An empty snapshot list is expected. Upload permission is tested in step 9. If installation or bucket access fails, fix that checkpoint before starting the stack.

### If recovery Python setup fails

The installer prints the failing stage and the path to a private log under
`/var/lib/athenaeum/install-rollback-*`. Read the exact file it names with
`sudo less /path/to/log`. The log preserves Python/pip output; review it locally
before sharing because package-index configuration can contain credentials.

If the creation log reports missing `ensurepip` or the `venv` package, install
Ubuntu's package, then retry:

```bash
sudo apt update
sudo apt install python3-venv
sudo ops/runbook/install-services --apply
```

`install-services --apply` now checks and installs missing Ubuntu venv support before invoking the installer. Retries repair a partially created Python environment and check that pip works.
Your existing configuration, session key, and app records are preserved. There
is no need to rerun `configure-host` or delete the environment.

With an older installer that prints only `python3 failed or timed out`, expose
the underlying creation error directly:

```bash
sudo python3 -m venv /opt/athenaeum/operations/venv
```

If that succeeds, retry `install-services --apply`. If it fails, use its actual
error to resolve the cause; a missing package is only one possibility.

## 6. Build and publish images locally

**Browser — Docker Hub:** Create or reuse the public `valentemath/athenaeum` and `valentemath/quacktuaries` repositories. Public images let the VM pull without registry credentials. If you use a private repository, run `sudo docker login --username valentemath` on the VM with a read-only token before pulling.

Your Fish entries in `~/.config/builder/builds.yaml` build independently: `athenaeum` builds only the site and edge; `quacktuaries` uses its own checkout, recipe and version counter. Both default to ARM64 for Oracle.

**Your computer — Fish:**

```fish
docker login --username valentemath
build --dry-run --version v0.1.0 athenaeum
build --version v0.1.0 athenaeum
build --dry-run --version v0.1.0 quacktuaries
build --version v0.1.0 quacktuaries
```

Use an explicit version for each first release. Later use `build athenaeum` or `build quacktuaries` for the project you changed; only its version increments. Tests and builds read current local files, including uncommitted changes. Test classroom changes with `ops/local-stack` before publication and save reviewed source in Git. If pushing Quacktuaries source still triggers its old Cloud Run deployment, disable that trigger before the Git push and preserve required live records first.

| Service | Moving image reference | Example version |
|---|---|---|
| Website | `valentemath/athenaeum:latest` | `valentemath/athenaeum:v0.1.0` |
| Caddy edge | `valentemath/athenaeum:latest-edge` | `valentemath/athenaeum:v0.1.0-edge` |
| Quacktuaries on this site | `valentemath/quacktuaries:latest-athenaeum` | `valentemath/quacktuaries:v0.1.0-athenaeum` |

The same `build quacktuaries` also publishes `valentemath/quacktuaries:latest` with root-path defaults for use outside Athenaeum. Both variants share one Quacktuaries version and publish together, like Tailgate's two image lines. The Athenaeum variant defaults to production and `/quacktuaries`, while runtime secrets and proxy trust remain in this site's Compose file. Athenaeum's version is independent.

On an AMD64 workstation, Quacktuaries needs ARM emulation during build. If `/proc/sys/fs/binfmt_misc/qemu-aarch64` is missing after reboot, register it locally:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

This registration lasts for the current boot. [Docker cross-platform builds](https://docs.docker.com/build/building/multi-platform/).

**Checkpoint:** Both project builds and their pushes succeeded. A partial push may leave an individual project incompletely published; finish that build before updating the VM. `--no-push --version v0.1.0` is available for a local build test that does not advance shared version state.

## 7. Point DNS at Oracle

**Browser — Cloudflare:** Export the current records, then set:

| Type | Name | Value | Proxy |
|---|---|---|---|
| A | `@` | Reserved VM IPv4 | DNS only |
| CNAME | `www` | `valentemath.com` | DNS only |

Use Auto TTL. Resolve conflicts and stale AAAA records only for these two names. Preserve email records and `quacktuaries.valentemath.com`. Part 1's OCI security rules must allow TCP 80/443; keep administrator SSH limited to your workstation IP.

**Your computer:**

```bash
getent ahostsv4 valentemath.com
getent ahostsv4 www.valentemath.com
```

**Checkpoint:** Both names resolve to the reserved IPv4. Caddy obtains certificates when the stack starts; DNS and incoming ports must work first. [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

## 8. Start and verify the stack

**VM:**

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/stack start
sudo ops/runbook/verify-site
```

`start` checks the data mount and metadata guard, pulls the configured images, then runs `docker compose up -d --no-build --pull never --wait`. It is for an empty first installation. Once app data exists, use `stack update`, which backs up before replacing containers. `stack pull` only downloads images and can be used independently.

`verify-site` checks all three container health states, HTTPS for the site and app, exact redirects for `www` and the bare app prefix, and denied container access to Oracle metadata. It does not create classroom records.

**Browser checkpoint:** Open `https://valentemath.com/`, follow Projects → Quacktuaries, create a synthetic teacher/game, join as a synthetic student in another profile, start the game, perform an action, end it, and export its CSV. The current teacher login is name/cookie based. Leave the test game ended.

A health/HTTPS failure stops the script without deleting data or attempting automatic rollback. Inspect the affected container's logs. If a failed first start already created records, use `stack update`; if the backup cannot inspect all three containers, resolve the missing-container/configuration issue deliberately before proceeding. Do not remove the database or signing key to bypass a checkpoint.

## 9. Prove recovery and save a copy outside Oracle

The recovery script makes a new verified backup, selects the exact current image automatically, restores into a new directory under `/var/tmp`, runs the recovered app with no network or published ports, and exports the encrypted archive. It holds the same lock as updates and scheduled backups.

First transfer the private identity temporarily:

**VM:**

```bash
install -d -m 0700 /home/ubuntu/.athenaeum-key-transfer
```

**Your computer — Bash:**

```bash
scp -i "$HOME/.ssh/athenaeum-oracle" \
  "$HOME/.ssh/athenaeum-backup-identity.txt" \
  ubuntu@REPLACE_VM_IP:.athenaeum-key-transfer/identity.txt
```

**VM:**

```bash
chmod 0600 /home/ubuntu/.athenaeum-key-transfer/identity.txt
sudo ops/runbook/check-recovery \
  --identity /home/ubuntu/.athenaeum-key-transfer/identity.txt \
  --export /home/ubuntu/athenaeum-first.age
```

Choose a new export filename on later drills; existing outputs are refused. The script uses a private `/run` copy of the key and removes that copy on normal success, failure, or interruption. The supplied file remains yours. **After the command finishes, including on failure**, remove the transferred file:

```bash
rm /home/ubuntu/.athenaeum-key-transfer/identity.txt
rmdir /home/ubuntu/.athenaeum-key-transfer
```

**Checkpoint:** The script prints `PASS`, snapshot ID, encrypted export path, and SHA256. Save those values in private recovery notes. Its restored inspection directory remains root-private; live data is unchanged. The original identity on your computer is unchanged. If the process is forcibly killed or the VM loses power, inspect `/run/athenaeum-key-*` for leftover private copies after recovery; `/run` clears at reboot.

**Your computer — Bash:** Use the reported checksum:

```bash
ops/runbook/download-backup \
  --host REPLACE_VM_IP \
  --remote /home/ubuntu/athenaeum-first.age \
  --sha256 REPLACE_REPORTED_SHA256 \
  --output "$HOME/Backups/athenaeum/athenaeum-first.age"
```

It checks the trusted SSH host key, downloads privately, verifies SHA256, and creates the final local filename only after verification. It refuses overwrite. Use `--ssh-key /your/key` if needed.

**Checkpoint:** `PASS` confirms an encrypted copy outside Oracle. Keep the identity independently, plus source commits and published image versions/digests in recovery notes. Repeat this drill periodically; an uploaded archive alone does not prove restoration.

## 10. Enable hourly backups and test reboot

**VM:**

```bash
sudo ops/runbook/enable-backups
sudo reboot
```

The enable script requires a recent successful restore drill and a passing current health/backup status. It enables only `athenaeum-backup.timer`.

Reconnect and run:

```bash
cd /opt/athenaeum/stack
sudo athenaeumctl preflight
sudo systemctl is-active docker athenaeum-metadata-guard.service
sudo systemctl list-timers athenaeum-backup.timer --no-pager
sudo ops/runbook/verify-site
sudo athenaeumctl status
```

**Checkpoint:** The exact data filesystem is mounted, guards and containers are healthy, and the backup timer has a future run. Open Quacktuaries and confirm the ended test game's records and browser access survived reboot.

## Routine maintenance

For an image update, test locally and publish the project you changed:

**Your computer — Fish:**

```fish
# Website or edge changes:
build athenaeum
# Quacktuaries changes (independent release):
build quacktuaries
```

Then, during a break in classroom use (from any VM directory):

**VM:**

```bash
athenaeumctl docker pull --deploy
athenaeumctl verify
```

The update script takes a verified backup first, pulls images, and recreates changed containers while preserving bind-mounted records and keys. Failure stops the sequence. The shared lock prevents overlap with backups/restores invoked through these tools; direct Docker commands do not take that lock. There is no automatic class-activity check, schema migration, or image rollback.

For Git-managed Compose changes, edit/commit/push on your workstation, then use:

```bash
athenaeumctl repo sync
athenaeumctl docker deploy
athenaeumctl verify
```

`repo sync` validates the fetched Compose configuration and fast-forwards a clean `main` checkout. It stops for local edits or diverged history and never changes containers. `docker deploy` backs up before applying Compose using already downloaded images; use `docker pull --deploy` if new image downloads are needed. If `ops/` changed, run `athenaeumctl self update` after sync to refresh installed tools without restarting Docker.

Use `athenaeumctl status`, `docker ps`, `docker logs quacktuaries -f`, `backup`, and `list` for daily checks. See the [full daily command reference](../delivery.md#daily-commands-on-the-vm). Keep settings and image overrides in `/etc/athenaeum`, outside Git.

For manual rollback, set the affected image override in `/etc/athenaeum/compose.env` to a compatible retained version, then run `athenaeumctl docker pull --deploy` and `athenaeumctl verify`. For example:

```dotenv
QUACKTUARIES_IMAGE=valentemath/quacktuaries:v0.1.0-athenaeum
```

An image rollback leaves the current database unchanged. Schema-incompatible releases need a separate [recovery plan](../recovery.md). Remove the override when ready to follow the moving tag again. [Delivery details](../delivery.md) include the command reference and old automation cleanup.

| Task | VM command |
|---|---|
| Current health and backup age | `athenaeumctl status` |
| Full diagnostics | `athenaeumctl status --json` |
| Backup now | `athenaeumctl backup` |
| List recovery points | `athenaeumctl list` |
| Container logs | `athenaeumctl docker logs --tail 50` |
| Backup service logs | `sudo journalctl -u athenaeum-backup.service -n 50 --no-pager` |

Monthly: check disk space, backup age, Oracle usage/account notices, and Ubuntu updates. Repeat step 9 with a new export filename. Retain published version images needed by backups. Review old `/var/tmp/athenaeum-restore-*` inspection copies before deleting only those you no longer need. Do not prune live volumes as routine maintenance.

## If a step stops

| Failure | Next action |
|---|---|
| Wrong host | Run the VM step in SSH on Ubuntu ARM; local key/download scripts run on your computer |
| Mount/fstab conflict | Inspect the named UUID and existing rule; preserve files and do not reformat |
| Docker package/source conflict | Review existing installation using Docker's official instructions |
| Partial/invalid settings | Edit the named `/etc/athenaeum` files; do not invent a new key beneath existing data |
| Bucket access denied | Recheck region, namespace, bucket settings, dynamic group, both IAM policies and propagation |
| Backup fails before update | Resolve the backup error; current containers have not been replaced |
| Pull fails | Finish the affected project's pushes; verify tags and ARM64 availability; log in for private images |
| Health/HTTPS fails | Inspect logs, DNS, OCI ports and certificate status; preserve data while correcting configuration |
| Operation lock busy | Let the current backup/restore/update finish, then retry |
| Export/output exists | Choose a new name; existing recovery copies are preserved |
| Restore fails | Keep the last known-good backup and image; inspect errors before enabling the schedule |

Local tests cannot prove Oracle permissions, actual ARM VM behavior, external DNS/HTTPS, or reboot recovery. The numbered checkpoints above are the acceptance test on your host. No script contacts Oracle or changes a host simply by viewing its source or running `--help`.

## Upgrade an existing VM to the command center

Use this once if you completed an earlier version of this runbook. The original `scp` setup may have no `.git` directory. This upgrade installs the new command launcher, then adopts or syncs Git while the existing containers keep running. No image rebuild or container redeployment is needed for this upgrade.

**VM — Bash:** Clone the published source into a separate bootstrap directory. If that directory already exists from an earlier attempt, inspect it and use a new directory name instead of overwriting it.

```bash
git clone https://github.com/mr-valente/athenaeum.git "$HOME/athenaeum-bootstrap"
sudo "$HOME/athenaeum-bootstrap/ops/install-host" \
  --config /etc/athenaeum/recovery.json --apply
```

This calls the installer directly, which does **not restart Docker**. Do not use `install-services --apply` for this upgrade: that first-time activation command restarts Docker. Existing configuration, session key, data and enabled backups are preserved. Replaced tool files have rollback copies under `/var/lib/athenaeum/install-rollback-*`. If the hourly backup currently holds the lock, let it finish and retry.

Choose the command matching the current stack directory:

```bash
if [ -e /opt/athenaeum/stack/.git ]; then
  athenaeumctl repo sync
else
  athenaeumctl repo adopt
fi
```

`repo adopt` clones `main`, validates its Compose configuration, and preserves the entire old copied directory under the private `/opt/athenaeum/.athenaeum-repo-*/previous-stack` path printed on success. It then puts the Git checkout at `/opt/athenaeum/stack`. Review any customizations in the preserved directory. Neither adoption nor sync replaces containers or changes `/etc` settings. An existing Git checkout with local changes stops for review instead of losing them.

The earlier `QUACKTUARIES_IMAGE=valentemath/quacktuaries:latest-athenaeum` override can remain in `/etc/athenaeum/compose.env`; it matches the new default. Do not put that file or any keys in Git.

Verify the result:

```bash
athenaeumctl repo status
athenaeumctl docker images
athenaeumctl verify
athenaeumctl status
```

**Checkpoint:** The checkout is clean on `main`, Quacktuaries resolves to its own `latest-athenaeum` image, HTTPS/health checks pass, and backups are current. Future source updates use `repo sync`; follow with `self update` for host-tool changes or a deployment command for Compose/image changes. Keep the bootstrap and preserved source copies until satisfied with the upgrade.
