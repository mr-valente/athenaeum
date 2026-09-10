# 4 — Backup and recovery

Keep an encrypted backup outside Oracle, its SHA256, the private age identity, source commits, and matching image versions/digests in your private recovery records. Store the identity separately from the archive. The VM's scheduled backups need only the public age recipient.

## Recovery contents and limits

Each verified snapshot contains:

- Each registered app's SQLite database (Quacktuaries, Bernoulli), captured through SQLite's online backup API.
- Each app's persistent session-signing key.
- Compose definitions, Compose environment, and recovery configuration.
- A manifest with file checksums, per-app database schema/counts, architecture and running image IDs/digests.

Caddy's certificate cache is not archived; certificates are reissued on a replacement host. The static website is recovered from Git and its image. Neither app has registered uploads. An app that has not started yet is archived as its key alone. Adding application data requires explicit backup/restore support. Snapshots made before Bernoulli's registration (manifest schema 1) remain restorable and contain only Quacktuaries.

Uploads are downloaded and checked by SHA256 before their commit object is written and verified. Only committed snapshots appear in `athenaeumctl list`. A restore drill separately proves decryption and application startup.

The private Standard bucket must have versioning Disabled, auto-tiering off, and no retention lock. The VM uses its instance principal, with bucket-scoped `read buckets` and `manage objects` permissions from [Guide 1](1-oracle-infrastructure.md). Apps receive no OCI credentials.

Defaults: 8,000,000,000-byte bucket cap, 1,000,000,000-byte encrypted snapshot limit, 4,000,000,000-byte expanded restore limit, and 2,000,000,000 bytes of disk headroom. Isolated restore reserves space for two expanded copies plus an encrypted copy and headroom: 11 GB with these defaults.

### Choose the bucket cap

Inspect the allowance applicable to your account and storage tier, then subtract storage used outside this bucket and a growth reserve. The cap is the **total permitted size of this bucket**, not extra space to add to its current usage. The tool already counts all objects and incomplete multipart parts within this bucket, including unrelated prefixes.

For example, with a 10 GB Standard allocation, 1 GB used in other buckets and a 1 GB reserve, use 8 GB (`8000000000` bytes). Use consistent byte units. This is an allocation example, not a tenancy-wide quota enforcement mechanism.

Oracle documents 20 GB combined Object Storage for expired-trial Always Free-only accounts, and tier-specific allowances including 10 GB Standard for paid/trial accounts. Check your account's current allowance before choosing the cap. Block and boot volumes use separate storage accounting. [Oracle Always Free storage](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## Prepare the recovery identity

On your Arch workstation:

```bash
sudo pacman -Syu --needed age
cd /home/nicholas/forge/athenaeum
ops/runbook/backup-key
```

The script creates or reuses `~/.ssh/athenaeum-backup-identity.txt` and prints its public recipient. Save the private identity in your password manager. For a replacement deployment, retrieve the existing identity; a newly generated key cannot decrypt existing backups. Independent duplicate systems can use separate identities and buckets.

## Recovery drill and offsite copy

Use this after first startup and periodically thereafter. It creates a verified backup, selects each app's current immutable image, restores into a new private `/var/tmp` directory, and tests a copy of every restored database in a non-root container with no network, published ports, or production mounts.

**VM:**

```bash
install -d -m 0700 "$HOME/.athenaeum-key-transfer"
```

**Workstation:**

```bash
scp -i "$HOME/.ssh/athenaeum-oracle" \
  "$HOME/.ssh/athenaeum-backup-identity.txt" \
  ubuntu@REPLACE_VM_IP:.athenaeum-key-transfer/identity.txt
```

**VM:** Use a new export name for every drill.

```bash
chmod 0600 "$HOME/.athenaeum-key-transfer/identity.txt"
sudo /opt/athenaeum/stack/ops/runbook/check-recovery \
  --identity "$HOME/.athenaeum-key-transfer/identity.txt" \
  --export "$HOME/athenaeum-first.age"
```

Stop on failure. On success, record the reported snapshot ID, SHA256, export path and restored inspection directory. The encrypted export belongs to the SSH operator for download. Remove the transferred identity:

```bash
rm -- "$HOME/.athenaeum-key-transfer/identity.txt"
rmdir -- "$HOME/.athenaeum-key-transfer"
```

**Workstation:**

```bash
ops/runbook/download-backup \
  --host REPLACE_VM_IP \
  --remote /home/ubuntu/athenaeum-first.age \
  --sha256 REPLACE_REPORTED_SHA256 \
  --output "$HOME/Backups/athenaeum/athenaeum-first.age"
```

The downloader checks the trusted SSH host key and checksum and refuses overwrite. Keep the verified local copy and checksum outside Oracle. The private identity stays independently recoverable. Save source commits and image digests in the same private inventory, not in this public repository.

After the first successful drill, enable scheduled backups on the VM:

```bash
sudo /opt/athenaeum/stack/ops/runbook/enable-backups
```

The script requires a recent successful restore drill and healthy current status. The timer runs hourly and catches a missed run after downtime. Repeating the enable command is safe.

## Retention and troubleshooting

After successful upload/commit verification, retain the newest snapshot in each of 24 UTC hour buckets and 7 UTC day buckets; overlapping selections mean at most 31 points. The newest verified snapshot is always retained. If the next snapshot exceeds the bucket cap, backup stops before uploading or pruning. Review data growth and storage allocation; do not delete the last recovery point to force an update through.

```bash
athenaeumctl status --json
athenaeumctl list
sudo systemctl list-timers athenaeum-backup.timer --no-pager
sudo journalctl -u athenaeum-backup.service -n 50 --no-pager
```

`status` reports stale backups and the last failed/interrupted attempt. Its separate `last_restore_test` entry records the drill. A successful upload alone is not proof of restoration. No external failure notifications are configured.

The shared operation lock prevents overlap among backups, restores, Git sync and supported deployments. Log following does not hold it. Direct Docker/file changes do not participate in the lock; use them only in a deliberate maintenance operation with the backup timer stopped and no active backup service.

Failed uploads can leave uncommitted ciphertext that counts toward the cap. The tool prunes only its own registered snapshots, never unrelated objects or someone else's multipart uploads. Review leftovers in Oracle and keep its incomplete-upload lifecycle cleanup configured. Plaintext staging is private and cleaned on success/failure; systemd and the next backup clean marked leftovers after forced termination.

## Disaster recovery

First preserve surviving resources and stop writes on any previous host before cutover. The same database must not be independently written by two live stacks; only one VM should write to a backup prefix. For an independent duplicate, use its own volume, bucket/prefix, identity and domain.

1. Inventory the surviving data volume, bucket, reserved IP, SSH key, recovery identity and external archive. Attach an existing ext4 volume without formatting it.
2. Validate the selected archive on your workstation using the next section. This provides the original signing key even when the old boot disk is lost.
3. Follow [Guide 1](1-oracle-infrastructure.md) for missing infrastructure. Update the dynamic group's instance OCID when replacing the VM. Retain the bucket when it survives.
4. Follow [Guide 2](2-host-setup.md) through host configuration. If app records survived on the volume, install their original signing key **before** service installation; the installer refuses to generate a new key beneath existing data.
5. Install host tools. For a fresh replacement disk, copy the validated database and its key as described below before starting containers. Reuse surviving live data when that is the chosen recovery source.
6. Pin compatible retained images, perform the Guide 2 startup and browser checkpoints, then make a new verified backup/restore drill. Enable the timer only after recovery is proved.

Archived configuration is evidence for reconstruction, not a script to execute. Re-enter the replacement disk's UUID, instance permissions, region/bucket and intended domain. Review image overrides against the selected backup. Preserve newer records before deliberately recovering an older point.

### Validate an external archive offline

This uses Python, age, the checked-out host code and an explicit local config. It requires no Oracle credentials, Docker or surviving data mount. Use a fresh private workspace with at least the configured restore headroom.

**Workstation — Bash**, from the Athenaeum checkout:

```bash
umask 077
mkdir "$HOME/athenaeum-recovery"
python3 - <<'PY'
import json, os, shutil, subprocess
from pathlib import Path
os.umask(0o077)
work = Path.home() / 'athenaeum-recovery'
for name in ('state', 'objects'):
    (work / name).mkdir(mode=0o700)
identity = Path.home() / '.ssh/athenaeum-backup-identity.txt'
recipient = subprocess.check_output(['age-keygen', '-y', str(identity)], text=True).strip()
age = shutil.which('age')
assert age, 'Install age first'
config = {
    'mode': 'local', 'filesystem_uuid': '', 'data_root': str(work / 'unused-live-root'),
    'state_dir': str(work / 'state'), 'recipient': recipient, 'age_binary': age,
    'store': {'kind': 'local', 'directory': str(work / 'objects')},
}
with (work / 'recovery.json').open('x') as output:
    json.dump(config, output, indent=2)
PY
ops/athenaeumctl --config "$HOME/athenaeum-recovery/recovery.json" restore \
  --archive "$HOME/Backups/athenaeum/REPLACE_SNAPSHOT.age" \
  --sha256 REPLACE_RECORDED_SHA256 \
  --target "$HOME/athenaeum-recovery/restored" \
  --identity "$HOME/.ssh/athenaeum-backup-identity.txt"
```

The target must not already exist. Validation checks ciphertext SHA256, archive paths/types/sizes, manifest checksums, SQLite integrity, foreign keys, schema and row counts. Restored files stay private. Adjust the config's documented size limits only when recovering a deliberately larger backup.

If only the Oracle copy survives, an installed replacement host with working instance-principal access can use `athenaeumctl list` and `athenaeumctl restore --snapshot ID --target NEW_PATH --identity PRIVATE_FILE`. If host installation is blocked by surviving app data with a missing signing key, first download the selected ciphertext and its committed checksum through the Oracle Console and use the offline procedure. Do not delete the surviving database to bypass that check.

### Transfer the recovered keys and databases

Repeat this section for each app listed under `applications` in `restored/manifest.json`. The commands show Quacktuaries; Bernoulli's files are `secrets/bernoulli-session-secret` and `apps/bernoulli/app.db`, installed at `/etc/athenaeum/bernoulli-session-secret` and `/srv/athenaeum/apps/bernoulli/data/app.db`. An app archived without a database has only a key to transfer.

**VM:** Before host service installation, prepare a private transfer directory:

```bash
install -d -m 0700 "$HOME/.athenaeum-recovery-transfer"
```

**Workstation:**

```bash
scp -i "$HOME/.ssh/athenaeum-oracle" \
  "$HOME/athenaeum-recovery/restored/secrets/quacktuaries-session-secret" \
  "$HOME/athenaeum-recovery/restored/apps/quacktuaries/app.db" \
  ubuntu@REPLACE_VM_IP:.athenaeum-recovery-transfer/
```

**VM:** After Guide 2 creates `/etc/athenaeum`, and before installing services, install each original signing key only if its destination is absent:

```bash
sudo test ! -e /etc/athenaeum/quacktuaries-session-secret && \
  sudo install -o 10001 -g 10001 -m 0600 \
    "$HOME/.athenaeum-recovery-transfer/quacktuaries-session-secret" \
    /etc/athenaeum/quacktuaries-session-secret
```

If the test fails, stop and check the existing key against the recovery source; do not overwrite a key used by surviving records. If it is already the correct key, retain it and continue. Complete Guide 2's service installation to prepare owned directories, but leave the containers and timer unstarted. The installer generates a fresh key only for an app whose key is absent and whose data directory is empty.

On a **fresh, empty replacement data directory**, install each validated database:

```bash
sudo python3 - <<'PY'
from pathlib import Path
path = Path('/srv/athenaeum/apps/quacktuaries/data')
assert path.is_dir() and not any(path.iterdir()), 'Data already exists: preserve/review it first'
PY
sudo install -o 10001 -g 10001 -m 0600 \
  "$HOME/.athenaeum-recovery-transfer/app.db" \
  /srv/athenaeum/apps/quacktuaries/data/app.db
```

Stop if the empty-directory check fails. Surviving-volume recovery keeps its existing database and any SQLite WAL files together; it does not use this copy step. Replacing an existing database is a separate maintenance operation: stop the stack/timer, preserve the entire existing data directory, then install a validated database/key pair. Never copy a lone live SQLite file or overwrite it with active writers.

Choose known compatible retained images from your recovery records and confirm them against `restored/manifest.json`. Set explicit image overrides in `/etc/athenaeum/compose.env` before Guide 2 pulls or starts anything. Use the saved repository digest when available. Archive content does not automatically choose executable code. Keep the signing keys stable so recovered browser sessions retain ownership.

After startup, verification and a fresh recovery drill pass, remove the transferred plaintext database/key and private restore workspace when no longer needed. Keep the encrypted external backup, checksum and private age identity.

## Test a selected snapshot in isolation

On a prepared VM, choose a snapshot from `athenaeumctl list`, explicitly pull the matching immutable image of every app whose database it holds, and provide the private age identity as a mode-0600 file:

```bash
athenaeumctl restore-test \
  --snapshot REPLACE_SNAPSHOT_ID \
  --target /var/tmp/athenaeum-restore-REPLACE_UNIQUE_NAME \
  --identity /run/athenaeum-restore-identity \
  --image quacktuaries=REPLACE_IMMUTABLE_IMAGE_ID_OR_REPOSITORY_DIGEST \
  --image bernoulli=REPLACE_IMMUTABLE_IMAGE_ID_OR_REPOSITORY_DIGEST
```

Each image must already be present and match the snapshot's record for that app. Supply exactly one `--image APP=…` per restored database; a bare reference is accepted only for a snapshot holding a single database, such as one made before Bernoulli's registration. The test runs each app in turn on a copy of its recovered data in an isolated container and leaves the validated restore directory for inspection. `restore` validates data without executing an image; use it first when reviewing a rebuild or architecture change. Remove temporary private identities after use.

## Image rollback

Choose a retained version compatible with the current database. Edit `/etc/athenaeum/compose.env`, for example:

```dotenv
QUACKTUARIES_IMAGE=valentemath/quacktuaries:v0.1.0-athenaeum
```

Then run `athenaeumctl docker pull --deploy` and `athenaeumctl verify`. Use `BERNOULLI_IMAGE`, `ATHENAEUM_IMAGE` or `EDGE_IMAGE` for the other services. Remove a pin when ready to follow the moving tag again. This takes a backup but does not reverse database migrations; incompatible schemas need the deliberate data-recovery procedure above.

## Cleanup after a drill or recovery

After verifying the offsite copy and recovered applications, remove only the specific temporary identity, export, transfer directory or restored inspection directory you created. Restored plaintext includes the signing keys and configuration. Inspect directory contents and choose exact paths; do not use broad wildcards against live storage. Keep `/srv/athenaeum`, `/etc/athenaeum`, installed operations/tools, the state directory, retained Docker images and the independent recovery identity.

Installer rollback directories under `/var/lib/athenaeum/install-rollback-*` can contain private configuration. Retain the recent one while verifying a tool update; remove selected older copies only after confirming their replacement works. Deleting files is cleanup, not guaranteed forensic erasure from storage snapshots.
