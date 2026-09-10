# 2 — Host setup

Start after [Guide 1](1-oracle-infrastructure.md): an Ubuntu 26.04 ARM VM, attached data volume, reserved IPv4, backup bucket, instance permissions and working SSH access. This guide prepares a fresh host and starts the stack. For disaster recovery, read [Guide 4](4-backup-and-recovery.md#disaster-recovery) first and use its original database/key instructions at the checkpoints below.

Keep a workstation terminal and a VM SSH session open. Workstation examples assume Arch Linux; `build` commands run in Fish. VM commands work in Bash. Substitute your domain, VM address, resource identifiers and existing SSH key. Stop when a command or checkpoint fails.

## 1. Clone the host repository

**VM:**

```bash
sudo apt-get update
sudo apt-get install -y git
sudo install -d -o ubuntu -g ubuntu /opt/athenaeum
git clone https://github.com/mr-valente/athenaeum.git /opt/athenaeum/stack
/opt/athenaeum/stack/ops/runbook/check-host
```

If the stack directory already exists, inspect it and preserve local work. Keep it as a clean Git checkout. The VM does not need the Quacktuaries or Bernoulli source. Configuration and secrets live in `/etc/athenaeum`; data lives on the separate volume.

**Checkpoint:** The script reports Ubuntu 26.04, `aarch64`, Python, and the block devices. Match the attached data disk to the size and attachment recorded in Oracle.

## 2. Mount the data volume

**VM — Bash:** Set the device only after checking the block-device inventory:

```bash
ATHENAEUM_DEVICE=/dev/REPLACE_VERIFIED_DATA_DISK
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS "$ATHENAEUM_DEVICE"
sudo wipefs --no-act "$ATHENAEUM_DEVICE"
sudo blkid "$ATHENAEUM_DEVICE"
```

It must be the separate data disk, never a device containing `/`, `/boot` or `/boot/efi`. A blank disk has no filesystem signatures; `blkid` may return nonzero for it. An existing ext4 data volume must be reused without formatting. Investigate unexpected filesystems or records before proceeding.

**Only for a verified new blank disk:**

```bash
sudo mkfs.ext4 -L athenaeum-data "$ATHENAEUM_DEVICE"
sudo blkid "$ATHENAEUM_DEVICE"
```

Record its filesystem UUID, not `PARTUUID` or the volume OCID. Preview and apply the mount:

```bash
sudo /opt/athenaeum/stack/ops/runbook/mount-data --uuid REPLACE_FILESYSTEM_UUID
sudo /opt/athenaeum/stack/ops/runbook/mount-data --uuid REPLACE_FILESYSTEM_UUID --apply
sudo reboot
```

The script verifies ext4, refuses conflicting mounts/fstab entries and nonempty mountpoints, and saves the previous fstab before adding the UUID mount. After reconnecting:

```bash
sudo /opt/athenaeum/stack/ops/runbook/mount-data --uuid REPLACE_FILESYSTEM_UUID
```

**Checkpoint:** `/srv/athenaeum` is mounted with the same UUID after reboot. Do not format again if a mount check fails. [Oracle UUID mounting guidance](https://docs.oracle.com/en-us/iaas/Content/Block/References/fstaboptions.htm).

## 3. Install Docker and prerequisites

**VM:**

```bash
sudo apt update
sudo apt upgrade
sudo /opt/athenaeum/stack/ops/runbook/install-docker
sudo /opt/athenaeum/stack/ops/runbook/install-docker --apply
```

If Ubuntu requires a reboot, reboot and verify the mount again before continuing. The installer configures Docker's official Ubuntu repository, Docker CE, Compose, Python venv support and host prerequisites. It checks for conflicting packages/repositories and displays its plan before applying changes. [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/).

**Checkpoint:** Docker's greeting succeeds, Compose reports its version, and `DOCKER-USER` exists. Keep the iptables backend and `live-restore` disabled for the metadata and disk guards.

## 4. Configure the host

First [prepare or retrieve the age recovery identity](4-backup-and-recovery.md#prepare-the-recovery-identity) on your workstation. Keep the private identity outside the VM except during a restore/drill. Have its public recipient and the values from Guide 1 ready.

**VM:**

```bash
sudo /opt/athenaeum/stack/ops/runbook/configure-host
```

| Prompt | Value |
| --- | --- |
| Site domain | `valentemath.com`, or the duplicate installation's domain |
| ACME contact email | Your certificate-notice email |
| Public age recipient | The matching `age1...` public recipient |
| Oracle region | Full identifier, e.g. `us-ashburn-1` for Ashburn/IAD |
| Object Storage namespace | Namespace recorded in Oracle |
| Backup bucket | `athenaeum-backups`, or the installation's dedicated bucket |
| Backup bucket cap | Your allocation in bytes; see [Guide 4's calculation](4-backup-and-recovery.md#choose-the-bucket-cap) |

The script discovers the mounted UUID, validates Compose and writes `/etc/athenaeum/compose.env` and `/etc/athenaeum/recovery.json` with mode 0600. Existing complete settings are validated and preserved. Edit partial/incorrect files deliberately; do not replace them with guessed values.

**Recovery checkpoint:** If restoring an installation, [install the original signing keys](4-backup-and-recovery.md#transfer-the-recovered-keys-and-databases) now. In particular, surviving app data requires its key before service installation. For a new empty installation, the installer creates a new persistent key for each app.

## 5. Install host services and the command center

**VM:**

```bash
sudo /opt/athenaeum/stack/ops/runbook/install-services
sudo /opt/athenaeum/stack/ops/runbook/install-services --apply
```

The script installs the isolated OCI SDK environment, checksum-pinned age tools, command launcher, per-app data directories and signing keys, and service units. It checks Python venv support and repairs a partial environment on retry. It activates metadata protection and restarts Docker to apply the UUID guard, then checks bucket read access through the instance principal.

`athenaeumctl` is now on your normal PATH and works from any directory. It requests sudo automatically through your existing administrator policy; a password prompt may appear. Host configuration and secrets remain private.

**Checkpoint:** Disk and metadata guards are active, bucket read access succeeds, and apps and the backup timer are still unstarted. An empty snapshot list is normal for a new bucket. Installation failures identify the stage and a private log under `/var/lib/athenaeum/install-rollback-*`; inspect that log locally and retry the failed step.

For a fresh replacement disk, [install the validated recovered databases](4-backup-and-recovery.md#transfer-the-recovered-keys-and-databases) now. Keep existing records on a surviving volume. Do not start containers until each selected database and its original key are in place.

## 6. Select and download images

The VM uses these defaults:

| Service | Image |
| --- | --- |
| Website | `valentemath/athenaeum:latest` |
| Edge | `valentemath/athenaeum:latest-edge` |
| Quacktuaries | `valentemath/quacktuaries:latest-athenaeum` |
| Bernoulli | `valentemath/bernoulli:latest-athenaeum` |

For a new system, use published images or follow [image builds](../development/image-builds.md) to publish from the workstation. Athenaeum, Quacktuaries and Bernoulli have independent releases. Public repositories need no VM registry credentials; private ones require `sudo docker login` with pull access.

For disaster recovery, set compatible retained versions/digests from the selected backup in `/etc/athenaeum/compose.env` before pulling. Its `ATHENAEUM_IMAGE`, `EDGE_IMAGE`, `QUACKTUARIES_IMAGE` and `BERNOULLI_IMAGE` overrides take precedence over Compose defaults. Keep the matching image releases available independently of the old VM.

**VM:**

```bash
athenaeumctl docker images
athenaeumctl docker pull
```

**Checkpoint:** All four expected ARM64 images download successfully. No containers have been started.

## 7. Configure DNS

In Cloudflare, export existing DNS records, then configure the chosen domain:

| Type | Name | Value | Proxy |
| --- | --- | --- | --- |
| A | `@` | Reserved VM IPv4 | DNS only |
| CNAME | `www` | `valentemath.com`, or the chosen apex | DNS only |

Use Auto TTL. Resolve conflicting/stale A or AAAA records for those names and preserve unrelated records, including email. Oracle must allow inbound TCP 80/443; keep SSH limited to your workstation address. For disaster cutover, stop the previous stack's writes before directing users to the replacement.

**Workstation:** Substitute the chosen domain:

```bash
getent ahostsv4 valentemath.com
getent ahostsv4 www.valentemath.com
```

**Checkpoint:** Both names resolve to the reserved IPv4. Caddy obtains certificates when the stack starts. [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

## 8. Start and verify

**VM:**

```bash
athenaeumctl preflight
athenaeumctl start
athenaeumctl verify
```

`start` is for the prepared initial or recovered installation: it checks the mount and metadata guard, starts already downloaded images and waits for health. It does not take a backup. Once operating, use the backup-protected deployment commands in [Guide 3](3-daily-usage.md).

`verify` checks container health, site/app HTTPS, `www` and app-prefix redirects, and denied container metadata access. If it fails, inspect `athenaeumctl docker logs --tail 100` and the relevant DNS/firewall/configuration checkpoint. Preserve records and keys while correcting problems.

**Browser checkpoint:** Open the site, follow Projects → Quacktuaries, create a synthetic teacher/game, join as a synthetic student in another profile, start, perform an action, end the game and export its CSV. Then follow Projects → Bernoulli, create a Flip Flop session, join as a student, flip a coin, vote, lock, reveal, end the session and export its CSV. Leave both test sessions ended. After recovery, also verify expected restored records and ownership.

## 9. Prove backups and reboot behavior

Complete [Guide 4's recovery drill and offsite copy](4-backup-and-recovery.md#recovery-drill-and-offsite-copy), including its final backup-timer enable command. Return here once the archive has restored successfully and the workstation copy has passed its checksum check.

**VM:**

```bash
sudo reboot
```

Reconnect and check:

```bash
athenaeumctl preflight
sudo systemctl is-active docker athenaeum-metadata-guard.service
sudo systemctl list-timers athenaeum-backup.timer --no-pager
athenaeumctl verify
athenaeumctl status
```

**Final checkpoint:** The correct data volume is mounted after reboot; the site and both apps are healthy over HTTPS; container metadata access is denied; backups are current and scheduled; and a verified recovery copy and independent identity are available outside Oracle. Continue with [Guide 3 — Daily usage](3-daily-usage.md).
