# Backup and recovery

Phase D provides host-side SQLite snapshots, age encryption, native OCI object storage, bounded retention, isolated restore tests, and disk guards. It does not deploy the site, alter cloud resources, or migrate Cloud Run data. Images are built on the workstation and [updated manually with Compose](delivery.md).

## What a recovery point contains

- Quacktuaries SQLite database, captured with SQLite's online backup API.
- Its persistent session-signing key.
- The Compose environment, Compose definitions, and recovery configuration.
- A manifest with schema version/hash, table counts, file checksums, image IDs/available repository digests, and architecture.

There are no uploads in the current application. Additional files in its data directory are rejected until a consistent backup hook is added. Caddy certificates remain persistent on the live disk; they are deliberately **reissued** during disaster recovery rather than copied from an actively changing certificate cache. Static site files and container images are rebuilt or pulled from pinned releases, not archived.

Snapshots are compressed and encrypted to an age **public recipient**. The backup VM never needs the private decryption identity for scheduled backups. Keep that identity in your password manager and supply a protected file only for restores.

A successful upload is downloaded again and checked byte-for-byte by SHA256. Its small commit object is written and read back only after verification. Interrupted or unverified uploads are not listed as valid recovery points. This verifies ciphertext storage; a successful `restore-test` separately proves decryption and application recovery.

## Prepare Oracle permissions

Use the private **Standard** bucket from the Oracle setup guide. Keep versioning **Disabled** (not Suspended), auto-tiering off, and retention locks off. The tool rejects incompatible bucket settings rather than miscounting hidden object versions.

The guide's bucket-scoped `manage objects` policy covers snapshots, downloads, and retention. Add a second policy statement so the tool can inspect bucket settings:

```text
Allow dynamic-group id REPLACE_DYNAMIC_GROUP_OCID to read buckets
in compartment id REPLACE_COMPARTMENT_OCID
where target.bucket.name = 'athenaeum-backups'
```

No bucket creation, S3 key, user API key, or cloud credential goes into application containers. The SDK authenticates as the VM's instance principal. IAM/region/bucket access has not been tested against your account during local implementation.

## Prepare keys and configuration

On your trusted local computer, install [age](https://github.com/FiloSottile/age#installation), then generate a recovery identity into a private directory:

```bash
umask 077
age-keygen -o athenaeum-backup-identity.txt
age-keygen -y athenaeum-backup-identity.txt
```

Store the identity in your password manager. The second command prints the **public** recipient; put only that recipient in the VM configuration.

On the prepared VM, copy `deploy/recovery.example.json` to `/etc/athenaeum/recovery.json` with mode `0600`. Fill in every `REPLACE_…` value. Set `filesystem_uuid` to the mounted ext4 data disk UUID. Check it with `findmnt -no UUID --mountpoint /srv/athenaeum`.

Copy `deploy/production.env.example` to `/etc/athenaeum/compose.env` and set the domain and contact email. Keep both configuration files root-owned and mode 0600. The installer generates a strong session key for an empty new installation, owned by UID/GID 10001 and mode 0600. It preserves existing keys and refuses to invent a replacement underneath existing records.

Normal setup needs only the five values in `deploy/recovery.example.json`: filesystem UUID, public recipient, region, namespace, and bucket. Paths, retention limits, and tool locations have defaults in `ops/athenaeum_ops/common.py`.

The default bucket cap is **8 GB**, maximum snapshot **1 GB**, expanded restore **4 GB**, and reserved disk headroom **2 GB**. Add `bucket_cap_bytes` to `recovery.json` if your remaining Oracle allowance is smaller. Advanced path/size overrides remain available for tests and recovery; ordinary setup does not need them.

The storage cap counts **every object in this bucket**, including unrelated prefixes, plus incomplete multipart parts and the next snapshot/commit. Only registered snapshots in the owned prefix are pruned. Other buckets and services are outside this accounting; adjust the cap for their usage. Use one host writer and reserve upload headroom. No client-side check can enforce a tenancy-wide quota against unrelated concurrent writers.

## Install host files

Run on the prepared Oracle Ubuntu host. The current [Part 2 guide](guides/2-oracle-setup-guide.md) targets the operator's Ubuntu 26.04 ARM VM; the installer uses its `python3` in a private virtual environment. Docker, `python3-venv`, `iptables`, the mounted ext4 data disk, and production configuration must already exist. No filesystem formatting, mounting, resource provisioning, DNS change, or account upgrade happens here.

```bash
sudo ./ops/install-host --config /etc/athenaeum/recovery.json
sudo ./ops/install-host --config /etc/athenaeum/recovery.json --apply
```

The first command previews paths after checking the exact mount. The second installs a private operations virtual environment with a pinned OCI SDK dependency set, checksum-pinned age 1.3.2 for AMD64/ARM64, `athenaeumctl`, service/timer definitions, and Docker's UUID guard. It creates missing owned data directories only after the mount check. Existing files are saved under `/var/lib/athenaeum/install-rollback-…` before replacement; existing data and secrets are preserved. It rejects unexpected ownership rather than recursively changing live files. Review the retained rollback files before reversing an installation; the Python environment is reproducible from its saved lockfile, not a transactional environment rollback.

The backup service runs as root because it reads protected data/keys and inspects Docker image metadata. It uses a separate operations environment, a read-only system view, private temporary space, bounded memory/time, and restricted writable directories. The public apps have neither OCI credentials nor the Docker socket.

The installer **does not enable timers, start apps, or restart Docker**. Activate these during the intended host setup/maintenance window:

```bash
sudo systemctl enable --now athenaeum-metadata-guard.service
sudo athenaeumctl preflight
sudo systemctl cat docker
# After checking the added guard, restart Docker during the maintenance window:
sudo systemctl restart docker
sudo athenaeumctl guard-mount
```

The Docker drop-in preserves the guide's mountpoint guard and adds exact UUID validation. It binds Docker to `srv-athenaeum.mount`, so systemd stops Docker when that mount unit disappears. Docker `live-restore` must be disabled. A manual Docker/Compose invocation by root can bypass the supported preflight; run `athenaeumctl preflight` before the manual Compose commands in [delivery.md](delivery.md). The optional `athenaeumctl start` command checks the mount and metadata guard and starts already-pulled images; it never pulls or builds.

The metadata guard adds one idempotent `DOCKER-USER` rule rejecting Docker-forwarded traffic to `169.254.169.254`. It leaves host OUTPUT and unrelated firewall rules intact. This implementation requires Docker's iptables backend, not the alternative nftables backend. Verify container metadata denial and successful host instance authentication on Oracle before deployment; local tests do not establish either behavior there.

Once the configured images exist locally and the stack is intentionally ready to start:

```bash
sudo athenaeumctl start
sudo athenaeumctl backup
sudo athenaeumctl list
sudo athenaeumctl status
# Enable hourly backups only after the first manual backup and restore test pass:
sudo systemctl enable --now athenaeum-backup.timer
```

The timer runs hourly and catches a missed run after downtime. Watch failures with `systemctl --failed`, `journalctl -u athenaeum-backup.service`, and `athenaeumctl status`. No external notification is configured. `status` returns nonzero for stale backups, the last failed/interrupted attempt, or storage preflight failure. Successful status is not proof of a recent restore drill; check its separate `last_restore_test` entry.

## Retention and failures

After a new snapshot and commit pass readback verification, retain the newest point in each of the newest 24 UTC hour buckets and 7 UTC day buckets. This is at most 31 points; the hourly and daily selections overlap. The newest verified snapshot is always retained.

If the next upload will exceed the cap, the backup fails before uploading or deleting anything. It does not delete the last good point to make room. Reduce data size, deliberately free reviewed obsolete objects, or change the allocation only after checking your allowance. A failed upload/commit can leave uncommitted ciphertext; it counts toward the cap and requires deliberate inspection. The tool never deletes unrelated objects or aborts someone else's multipart upload. Keep Oracle's incomplete-upload lifecycle cleanup configured.

Backups, restores, and supported starts serialize on `/var/lib/athenaeum/operation.lock`. Manual Compose commands do not participate in that lock; avoid concurrent updates and backup/restore operations. Take a verified backup before changing Quacktuaries images and plan schema changes explicitly.

Plaintext staging is private and removed on success/failure. `ExecStopPost` and the next backup clean marked leftover staging after forced termination. Cleanup checks the mount and owner; it never searches or deletes arbitrary directories. File unlinking is cleanup, not guaranteed forensic erasure from SSDs/snapshots.

## Isolated restore test

For the normal operator drill, follow Part 2's `ops/runbook/check-recovery` and `download-backup` steps. They make a fresh backup, select the current immutable image, test recovery in isolation, export ciphertext, and verify the workstation copy. The commands below remain available for restoring a specifically chosen older snapshot.

Choose an explicit snapshot ID from `athenaeumctl list`. Obtain the matching Quacktuaries image from your recorded release and inspect its immutable ID/digest. Restores never choose or execute an image named only by untrusted archive content and never pull automatically.

Provide the private age identity as an operator-owned mode-0600 file, preferably under `/run` for the duration of the drill. Do not paste it into a command, environment variable, or log.

```bash
sudo athenaeumctl restore-test \
  --snapshot REPLACE_SNAPSHOT_ID \
  --target /var/tmp/athenaeum-restore-REPLACE_UNIQUE_NAME \
  --identity /run/athenaeum-restore-identity \
  --image REPLACE_IMMUTABLE_QUACKTUARIES_IMAGE_ID_OR_DIGEST
```

The target must be new and outside the live data root. The tool downloads the committed ciphertext, verifies its checksum, decrypts, rejects traversal/links/duplicates and oversized archives, verifies every manifest file, then runs SQLite integrity, foreign-key, schema, and row-count checks. It starts a **copy** of the recovered app data in a temporary non-root container with `--network none`, no published ports, and no production mounts. It checks health, the home page, and a saved session's HTTP state, then removes the test container. The validated restore directory remains private for inspection; the live database is untouched.

`restore` performs the same data validation without starting an image. `restore-test` additionally verifies runtime behavior. An image mismatch is refused. Keep the versioned Docker Hub image and its digest from the backup manifest, or retain an offline copy with `docker image save`. Pull the matching digest explicitly before testing on a replacement host. A rebuild or cross-architecture image requires separate compatibility review; use data-only `restore` first.

## External copy and recovery without the old VM

Download a ciphertext copy and save the reported SHA256 with your private recovery notes:

```bash
sudo athenaeumctl export --snapshot REPLACE_SNAPSHOT_ID \
  --output /var/tmp/athenaeum-REPLACE_SNAPSHOT_ID.age
```

Move that encrypted file to storage outside Oracle. Copy the public repository URLs, source commit SHAs and published image versions/digests, resource IDs, data UUID, and key location into your private recovery notes. An encrypted backup contains configuration/secrets, so retain its decryption identity independently.

On a replacement machine, prepare a private recovery config/state directory and install the compatible operations dependencies and age. Offline restore needs no bucket, cloud credential, Docker, or old data mount:

```bash
./ops/athenaeumctl --config /private/recovery.json restore \
  --archive /private/downloaded-snapshot.age \
  --sha256 REPLACE_RECORDED_SHA256 \
  --target /var/tmp/athenaeum-recovered-REPLACE_UNIQUE_NAME \
  --identity /private/athenaeum-backup-identity.txt
```

If the data volume survives, attach and mount its existing UUID; do not format it. If restoring to a new disk, first validate the backup in isolation. Then, in an explicit maintenance/cutover operation, stop the stack, preserve any current target data, copy the validated database and signing key to the reviewed paths with the proper ownership, and update configuration paths/image references for the replacement host. Never silently replace newer live writes with an old snapshot.

Update the instance-principal dynamic group for the replacement instance, verify its bucket access, reassign the reserved public IP, and reissue Caddy certificates only after data/application acceptance. Follow the Oracle guide for the VM and volume steps. The source backup's absolute paths are recovery evidence; they are not automatically executed or applied.

## Local verification

Local mode uses an explicitly configured private filesystem store; it cannot connect to OCI. It labels mount verification as false. Use `ATHENAEUM_TEST_AGE=/path/to/age` to run the real encryption tests:

```bash
ATHENAEUM_TEST_AGE=/path/to/age python3 -m unittest discover \
  -s tests -p 'test_*.py' -v
```

The sibling `age-keygen` binary must be present. Tests without age report the encryption cases as skipped, not passed. The suite covers concurrent WAL writing, mount/UUID rejection, locking, missing secrets, low disk/bucket space, corrupt downloads/manifests, wrong keys, traversal/links/duplicates, safe staging cleanup, retention, and OCI pagination/conditional requests using a fake transport.

During Phase D validation, all 21 Python tests passed with real age encryption enabled. A real age-encrypted snapshot of the Phase C synthetic database also restored successfully in an isolated native AMD64 app container. SDK calls were checked against the pinned Oracle SDK with networking disabled. Staged systemd unit definitions passed syntax/dependency validation with executable paths adjusted to the workspace. The host installer, IAM permissions, hourly timer, Docker mount-loss behavior, and metadata firewall have **not** been applied to this workstation or your Oracle VM. Native ARM execution and a reboot recovery drill remain first-deployment acceptance checks.

Sources: [SQLite online backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup), [age](https://github.com/FiloSottile/age), [OCI ObjectStorageClient](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/object_storage/client/oci.object_storage.ObjectStorageClient.html), [OCI multipart storage cleanup](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/managingobjects.htm#Multipart_Uploading_and_Downloading), [Oracle bucket/object permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/objectstoragepolicyreference.htm).
