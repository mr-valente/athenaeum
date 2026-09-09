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

Confirm you reached the expected machine using your known SSH host key. For the first source transfer, use the two terminals:

**VM:**

```bash
sudo install -d -o ubuntu -g ubuntu /opt/athenaeum
mkdir /opt/athenaeum/stack
```

If `stack` already exists, inspect it and preserve local changes instead of replacing it. For an existing deployment, see [old automation cleanup](../delivery.md#if-the-old-automation-was-already-installed) before updating the checkout or running these scripts.

**Your computer — Bash:** Copy only the host files needed by this runbook, including uncommitted script changes:

```bash
scp -i "$HOME/.ssh/athenaeum-oracle" -r compose.yaml deploy ops \
  ubuntu@REPLACE_VM_IP:/opt/athenaeum/stack/
```

This excludes `.state/`, private configuration, databases, and local backup keys. If the current source is already committed and available in Git, cloning Athenaeum to that same path is an alternative. The VM needs no Quacktuaries source checkout.

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

The first invocation previews the existing installer. Apply:

1. Installs the isolated OCI SDK environment, checksum-pinned age tools, and `athenaeumctl`.
2. Creates owned data directories and a strong session-signing key for an empty installation. Existing keys and data are preserved; replaced tool files have rollback copies under `/var/lib/athenaeum/install-rollback-*`.
3. Activates the metadata guard and **restarts Docker** to apply its exact-UUID disk guard.
4. Checks storage, the firewall rule, and actual Oracle bucket read access using the instance principal.

**Checkpoint:** Guards are active and `list` can read the bucket. No applications or backup timer have been enabled by this step. An empty snapshot list is expected. Upload permission is tested in step 9. If installation or bucket access fails, fix that checkpoint before starting the stack.

## 6. Build and publish images locally

**Browser — Docker Hub:** Create or reuse the public `valentemath/athenaeum` repository. Public images let the VM pull without registry credentials. If you use a private repository, run `sudo docker login --username valentemath` on the VM with a read-only token before pulling.

Your Fish entry in `~/.config/builder/builds.yaml` uses `athenaeum/docker/compose.yaml`. It builds this checkout and the sibling `~/forge/quacktuaries` checkout for ARM64.

**Your computer — Fish:**

```fish
docker login --username valentemath
build --dry-run --version v0.1.0 athenaeum
build --version v0.1.0 athenaeum
```

Use the explicit version for the first release; later use `build athenaeum` to increment the patch version. Tests and builds read current local files, including uncommitted changes. Test classroom changes with `ops/local-stack` before publication and save reviewed source in Git. If pushing Quacktuaries source still triggers its old Cloud Run deployment, disable that trigger before the Git push and preserve required live records first.

| Service | Moving image tag in `valentemath/athenaeum` | First version tag |
|---|---|---|
| Website | `latest` | `v0.1.0` |
| Caddy edge | `latest-edge` | `v0.1.0-edge` |
| Quacktuaries | `latest-quacktuaries` | `v0.1.0-quacktuaries` |

On an AMD64 workstation, Quacktuaries needs ARM emulation during build. If `/proc/sys/fs/binfmt_misc/qemu-aarch64` is missing after reboot, register it locally:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

This registration lasts for the current boot. [Docker cross-platform builds](https://docs.docker.com/build/building/multi-platform/).

**Checkpoint:** All builds and all six pushes succeeded. A partial push can leave the moving tags at different versions; finish publication before updating the VM. `--no-push --version v0.1.0` is available for a local build test that does not advance shared version state.

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

For an image update, test locally and publish all images:

**Your computer — Fish:**

```fish
build athenaeum
```

Then, during a break in classroom use:

**VM:**

```bash
cd /opt/athenaeum/stack
sudo ops/runbook/stack update
sudo ops/runbook/verify-site
```

The update script takes a verified backup first, pulls images, and recreates changed containers while preserving bind-mounted records and keys. Failure stops the sequence. The shared lock prevents overlap with backups/restores invoked through these tools; direct Docker commands do not take that lock. There is no automatic class-activity check, schema migration, or image rollback.

For manual rollback, set the affected image override in `/etc/athenaeum/compose.env` to a compatible retained version, then run `stack update` and `verify-site`. For example:

```dotenv
QUACKTUARIES_IMAGE=valentemath/athenaeum:v0.1.0-quacktuaries
```

An image rollback leaves the current database unchanged. Schema-incompatible releases need a separate [recovery plan](../recovery.md). Remove the override when ready to follow the moving tag again. [Delivery details](../delivery.md) include direct Compose equivalents and old automation cleanup.

| Task | VM command |
|---|---|
| Current health and backup age | `sudo athenaeumctl status` |
| Full diagnostics | `sudo athenaeumctl status --json` |
| Backup now | `sudo athenaeumctl backup` |
| List recovery points | `sudo athenaeumctl list` |
| Container logs | `sudo docker compose --env-file /etc/athenaeum/compose.env -f compose.yaml logs --tail 50` |
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
| Pull fails | Finish all local pushes; verify tags and ARM64 availability; log in for private images |
| Health/HTTPS fails | Inspect logs, DNS, OCI ports and certificate status; preserve data while correcting configuration |
| Operation lock busy | Let the current backup/restore/update finish, then retry |
| Export/output exists | Choose a new name; existing recovery copies are preserved |
| Restore fails | Keep the last known-good backup and image; inspect errors before enabling the schedule |

Local tests cannot prove Oracle permissions, actual ARM VM behavior, external DNS/HTTPS, or reboot recovery. The numbered checkpoints above are the acceptance test on your host. No script contacts Oracle or changes a host simply by viewing its source or running `--help`.
