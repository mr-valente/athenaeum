# 1 — Oracle infrastructure

A click-by-click Oracle Cloud Infrastructure (OCI) Console walkthrough for provisioning the Athenaeum host: one Ampere A1 VM, a separate data disk, a reserved public IPv4 address, a small public VCN, and private Object Storage backups.

> **Scope:** This document covers Oracle-side provisioning and the first connection checkpoint. It intentionally stops before formatting the data disk, installing Docker, deploying Athenaeum, or changing Cloudflare DNS.

Use this guide to provision an independent installation or replace lost infrastructure. For disaster recovery, first read [Guide 5](5-backup-and-recovery.md#disaster-recovery) and inventory surviving volumes, buckets, reserved addresses and keys. Reuse those resources where appropriate; replace only what was lost. A duplicate system needs distinct resource names, a domain, a volume, and its own backup bucket and identity.

---

## 1. Target architecture

```text
Athenaeum compartment
|
+-- athenaeum-vcn                  10.20.0.0/16
|   +-- athenaeum-igw
|   +-- athenaeum-public-routes
|   +-- athenaeum-public-security
|   +-- athenaeum-public           10.20.1.0/24
|
+-- athenaeum-arm                  VM.Standard.A1.Flex
|   +-- 2 OCPUs / 12 GB RAM
|   +-- Ubuntu 26.04 LTS, ARM64
|   +-- 50 GB boot volume
|   +-- athenaeum-public-ip        reserved public IPv4
|
+-- athenaeum-data                 separate block volume
|
+-- athenaeum-backups              private Standard bucket
|
+-- AthenaeumBackupVMs             dynamic group
+-- athenaeum-backup-objects       IAM policy
```

The public surface is SSH from your current public IP, plus HTTP/HTTPS for Caddy.

## 2. Confirm region, allowance, and existing storage

At the top of the OCI Console, select the tenancy's **home region**. Record the full region identifier: Ashburn is `us-ashburn-1`, also shown as `IAD`/`iad`. Availability and fault domains are separate fields. [Oracle region identifiers](https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm).

Use the Console search bar and open:

**Limits, Quotas and Usage**

Inspect Compute and Block Volume usage across compartments, including stopped instances, detached disks, and boot volumes.

### Planned resources

| Resource | Planned allocation |
|---|---:|
| Athenaeum ARM compute | `VM.Standard.A1.Flex`, 2 OCPUs, 12 GB RAM |
| Athenaeum boot volume | 50 GB |
| Athenaeum data volume | Size chosen after inventory; 100 GB only if total allowance permits |
| Other existing boot/block volumes | Count them before allocating storage |
| Object Storage backups | Budget about 8 GB initially |

> **Important:** A service limit is not a free-price guarantee. Review the current OCI cost estimate and account-specific allowance before creating resources.

### Record before continuing

- Home region
- Account type
- Tenancy OCID
- Existing boot + block volume total
- Available A1 capacity
- Available Standard Object Storage capacity

If the account is paid/PAYG, create a small monthly alert under **Billing & Cost Management -> Budgets**. A budget alert warns; it does not cap spending.

**Checkpoint:** You know how much A1 compute and block storage are actually available before creating anything.

---

## 3. Create the `Athenaeum` compartment

Navigate:

**Navigation menu -> Identity & Security -> Compartments**

Click **Create Compartment**.

| Field              | Value                                               |
| ------------------ | --------------------------------------------------- |
| Name               | `Athenaeum`                                         |
| Description        | `Resources for the Athenaeum web platform`          |
| Parent compartment | Your root tenancy                                   |
| Tags               | Leave blank unless you already use a tagging scheme |

Click **Create Compartment**.

> **Rule for the rest of this guide:** Unless a step says otherwise, every new Athenaeum resource should be created in the `Athenaeum` compartment.

**Checkpoint:** `Athenaeum` appears in the compartment list.

---

## 4. Create the dedicated SSH key locally

On your Arch workstation, open a terminal:

```bash
ssh-keygen -t rsa -b 4096 -f "$HOME/.ssh/athenaeum-oracle" -C athenaeum-oracle
```

Use a passphrase. Do not overwrite an existing key.

This creates:

```text
athenaeum-oracle       PRIVATE - keep on your computer
athenaeum-oracle.pub   PUBLIC  - upload this to Oracle
```

> **Never upload the private key.** Oracle receives only the `.pub` file.

---

## 5. Create the VCN manually

Navigate:

**Navigation menu -> Networking -> Virtual Cloud Networks**

Set the compartment filter to **Athenaeum**.

Click **Create VCN**.

> **Do not use Start VCN Wizard for this build.** The Athenaeum design needs only one public subnet and an Internet Gateway. Manual creation keeps the network small and auditable.

| Field                   | Value           |
| ----------------------- | --------------- |
| Name                    | `athenaeum-vcn` |
| Compartment             | `Athenaeum`     |
| IPv4 CIDR block         | `10.20.0.0/16`  |
| DNS hostnames           | Enabled         |
| DNS label, if requested | `athenaeum`     |
| IPv6                    | Off initially   |

Click **Create VCN**.

**Expected:** The VCN exists, but it does not yet have Internet access. We still need the Internet Gateway, route table, security list, and subnet.

---

## 6. Create the Internet Gateway

Open **athenaeum-vcn**.

Go to **Internet Gateways** (under Gateways or Resources, depending on the current Console layout).

Click **Create Internet Gateway**.

| Field                   | Value                           |
| ----------------------- | ------------------------------- |
| Name                    | `athenaeum-igw`                 |
| Compartment             | `Athenaeum`                     |
| Enabled                 | Yes                             |
| Route-table association | Leave default/unconfigured here |

Click **Create Internet Gateway**.

> The gateway alone does not route traffic. The next step creates the default route.

---

## 7. Create the public route table

Inside **athenaeum-vcn**, open **Route Tables**.

Click **Create Route Table**.

| Field       | Value                     |
| ----------- | ------------------------- |
| Name        | `athenaeum-public-routes` |
| Compartment | `Athenaeum`               |

Add a route rule:

| Field              | Value                       |
| ------------------ | --------------------------- |
| Target type        | Internet Gateway            |
| Destination CIDR   | `0.0.0.0/0`                 |
| Target compartment | `Athenaeum`                 |
| Target             | `athenaeum-igw`             |
| Description        | `Default route to Internet` |

Click **Create**.

**Checkpoint:** The route table contains `0.0.0.0/0 -> athenaeum-igw`.

---

## 8. Create the public security list

Inside **athenaeum-vcn**, open **Security Lists**.

Click **Create Security List**.

| Field       | Value                       |
| ----------- | --------------------------- |
| Name        | `athenaeum-public-security` |
| Compartment | `Athenaeum`                 |

### Ingress rules

All of these rule/s should be **stateful**. In the OCI Console, that normally means leaving **Stateless** unchecked.

| Source                | Protocol | Source port | Destination port | Purpose                                        |
| --------------------- | -------- | ----------- | ---------------: | ---------------------------------------------- |
| `YOUR_PUBLIC_IPV4/32` | TCP      | All         |               22 | SSH from your current Internet connection only |
| `0.0.0.0/0`           | TCP      | All         |               80 | HTTP / certificate bootstrap / redirect        |
| `0.0.0.0/0`           | TCP      | All         |              443 | HTTPS                                          |
| `0.0.0.0/0`           | ICMP     | -           |   Type 3, Code 4 | Path MTU discovery                             |

For example, if your current public IPv4 is `203.0.113.42`, enter:

```text
203.0.113.42/32
```

> **Do not open SSH to `0.0.0.0/0`.** If your home public IP changes later, update this `/32` rule.

### Egress rule

| Destination | Protocol      |
| ----------- | ------------- |
| `0.0.0.0/0` | All protocols |

Click **Create Security List**.

> **Do not add app or database ports.** The finished host exposes only Caddy on 80/443; internal services stay behind Docker networking.

---

## 9. Create the public subnet

Inside **athenaeum-vcn**, open **Subnets**.

Click **Create Subnet**.

| Field         | Value                       |
| ------------- | --------------------------- |
| Name          | `athenaeum-public`          |
| Compartment   | `Athenaeum`                 |
| Subnet type   | Regional                    |
| IPv4 CIDR     | `10.20.1.0/24`              |
| Access        | Public subnet               |
| Route table   | `athenaeum-public-routes`   |
| Security list | `athenaeum-public-security` |
| IPv6          | Off                         |
| DNS hostnames | Enabled is fine             |
| DHCP options  | Default                     |

If Oracle automatically selects `Default Security List for athenaeum-vcn`, remove it if the UI allows and associate **only** `athenaeum-public-security`.

Click **Create Subnet**.

### Networking checkpoint

```text
athenaeum-vcn
+-- athenaeum-igw
+-- athenaeum-public-routes
|   +-- 0.0.0.0/0 -> athenaeum-igw
+-- athenaeum-public-security
+-- athenaeum-public  10.20.1.0/24, public
```

No NAT Gateway. No private subnet. No load balancer.

---

## 10. Create the ARM VM

Navigate:

**Navigation menu -> Compute -> Instances**

Set the compartment filter to **Athenaeum** and click **Create Instance**.

### Basic information

| Field         | Value           |
| ------------- | --------------- |
| Name          | `athenaeum-arm` |
| Compartment   | `Athenaeum`     |
| Capacity type | On-demand       |

Do not choose preemptible capacity.

### Image

Click **Change Image**.

Choose a Canonical **Ubuntu 26.04 LTS** platform image compatible with **Arm / aarch64 / arm64**.

Use an ARM64/aarch64 image for the Ampere A1 shape.

### Shape

Click **Change Shape**.

Choose:

**Virtual Machine -> Ampere -> `VM.Standard.A1.Flex`**

Configure:

| Setting | Value |
|---|---:|
| OCPUs | 2 |
| Memory | 12 GB |

### Networking

Choose **Select existing virtual cloud network**.

| Field              | Value                                  |
| ------------------ | -------------------------------------- |
| VCN compartment    | `Athenaeum`                            |
| VCN                | `athenaeum-vcn`                        |
| Subnet compartment | `Athenaeum`                            |
| Subnet             | `athenaeum-public`                     |
| Private IPv4       | Automatically assign                   |
| Public IPv4        | **None / do not automatically assign** |
| IPv6               | Off                                    |
| NSGs               | None                                   |

> **Intentional:** Launch without an ephemeral public IP. We assign the reserved address directly after the instance is running.

### SSH key

Choose **Upload public key file (.pub)** and upload:

```text
athenaeum-oracle.pub
```

### Boot volume

Set the boot volume to **50 GB** if the Console requires an explicit size. Keep standard/balanced settings and Oracle-managed encryption. Do not attach the Athenaeum data disk here.

Review shape, image architecture, network, public-IP choice, and displayed cost estimate. Then click **Create**.

**Checkpoint:** Wait for lifecycle state **Running**.

---

## 11. Assign the reserved public IPv4 address

Open:

**Compute -> Instances -> athenaeum-arm -> Networking**

Under **Attached VNICs**, click the **primary VNIC**.

Open **IP administration**.

Find the row for the existing **primary private IPv4 address**.

Use that row's **Actions / three-dot menu -> Edit**.

> **OCI pitfall from the previous setup:** Do not choose **Assign secondary private IP address**. A public IP is attached to a private-IP object on the VNIC. We want the instance's existing primary private IP, not a new secondary one.

Set:

**Public IP type -> Reserved public IP -> Create new Reserved IP Address**

| Field             | Value                           |
| ----------------- | ------------------------------- |
| Name              | `athenaeum-public-ip`           |
| Compartment       | `Athenaeum`                     |
| IP address source | Default Oracle pool             |
| Route table       | Use VCN/subnet/VNIC route table |

Click **Update**.

### Record immediately

- Reserved public IPv4 address
- Instance OCID

The reserved address is designed to be movable to a replacement VM later.

---

## 12. Create the data block volume

Before creating it, re-check your current boot + block volume total.

Navigate:

**Navigation menu -> Storage -> Block Storage -> Block Volumes**

Click **Create Block Volume**.

| Field               | Value                                                 |
| ------------------- | ----------------------------------------------------- |
| Name                | `athenaeum-data`                                      |
| Compartment         | `Athenaeum`                                           |
| Availability Domain | **Same AD as `athenaeum-arm`**                        |
| Configuration       | Custom                                                |
| Size                | Use the allowance calculation; 100 GB only if it fits |
| Performance         | Balanced                                              |
| Encryption          | Oracle-managed                                        |
| Backup policy       | None initially                                        |
| Replication         | Off initially                                         |

> **Danger:** Do not accept a surprisingly large default size. Explicitly type the intended custom size.

Click **Create Block Volume**.

**Checkpoint:** The volume is **Available** and is in the same availability domain as the VM.

---

## 13. Attach the data volume to the VM

Return to:

**Compute -> Instances -> athenaeum-arm -> Storage / Attached block volumes**

Click **Attach Block Volume**.

Choose existing volume:

`athenaeum-data`

| Field | Value |
|---|---|
| Attachment type | Paravirtualized |
| Access | Read/write |
| In-transit encryption | Enable if offered |
| Device path | Accept/select as OCI requires |

Click **Attach** and wait for status **Attached**.

> **Stop here on the disk. Do not format anything in the Console.** The Linux-side procedure will identify the empty disk by size and attachment information before running `mkfs`.

---

## 14. Create the private Object Storage bucket

Navigate:

**Navigation menu -> Storage -> Object Storage & Archive Storage -> Buckets**

Set the compartment to **Athenaeum** and click **Create Bucket**.

| Field                | Value                      |
| -------------------- | -------------------------- |
| Name                 | `athenaeum-backups`        |
| Compartment          | `Athenaeum`                |
| Bucket scope         | Namespace, if shown        |
| Default storage tier | Standard                   |
| Visibility           | Private / no public access |
| Auto-tiering         | Disabled                   |
| Object versioning    | Disabled                   |
| Encryption           | Oracle-managed keys        |
| Retention lock       | None initially             |
| Replication          | None initially             |
| Multipart cleanup    | Leave enabled if offered   |

Click **Create**.

### Record

- Object Storage namespace
- Bucket name: `athenaeum-backups`
- Region
- Athenaeum compartment OCID

> This bucket is for encrypted application snapshots. It is not a Docker filesystem and is not mounted into the application.

---

## 15. Create the VM Dynamic Group

First copy the VM's OCID from:

**Compute -> Instances -> athenaeum-arm -> Instance information**

Then navigate:

**Identity & Security -> Domains**

Change to the *(root)* compartment for this task. Then choose:

**Default -> Dynamic Groups**

Click **Create Dynamic Group**.

| Field       | Value                                                      |
| ----------- | ---------------------------------------------------------- |
| Name        | `AthenaeumBackupVMs`                                       |
| Description | `Athenaeum VM authorized for backup Object Storage access` |
|             |                                                            |

Use this matching rule, substituting the real VM OCID:

```text
instance.id = 'REPLACE_INSTANCE_OCID'
```

Click **Create Dynamic Group**.

Record the new **dynamic-group OCID**.

> This build intentionally uses an instance-specific rule. If the VM is replaced, update the dynamic group's matching rule to the replacement instance OCID.

---

## 16. Create the backup IAM policy

Navigate:

**Identity & Security -> Policies**

Set the compartment to **Athenaeum** and click **Create Policy**.

| Field       | Value                                                        |
| ----------- | ------------------------------------------------------------ |
| Name        | `athenaeum-backup-objects`                                   |
| Description | `Allow the Athenaeum VM to access its private backup bucket` |
| Compartment | `Athenaeum`                                                  |

Choose **Show manual editor**.

Substitute the dynamic-group and compartment OCIDs in these statements:

```text
Allow dynamic-group id REPLACE_DYNAMIC_GROUP_OCID to manage objects
in compartment id REPLACE_COMPARTMENT_OCID
where target.bucket.name = 'athenaeum-backups'
```

```text
Allow dynamic-group id REPLACE_DYNAMIC_GROUP_OCID to read buckets
in compartment id REPLACE_COMPARTMENT_OCID
where target.bucket.name = 'athenaeum-backups'
```

Click **Create**.

Create the policy for the `Athenaeum` child compartment and use its OCID in both statements. When replacing a VM, update the dynamic-group rule to its new instance OCID.

Allow time for IAM/dynamic-group changes to propagate before diagnosing an authorization failure.

---

## 17. SSH to the new VM

From your workstation terminal, replace the IP with the reserved address recorded earlier:

```bash
ssh -i "$HOME/.ssh/athenaeum-oracle" ubuntu@REPLACE_VM_IP
```

For Canonical Ubuntu platform images, the default user is `ubuntu`.

If SSH times out, check in this order:

1. `athenaeum-public-ip` is assigned to the primary private IP.
2. `athenaeum-public` is a public subnet.
3. `athenaeum-public-routes` has `0.0.0.0/0 -> athenaeum-igw`.
4. Your current public IPv4 still matches the `/32` SSH ingress rule.
5. The subnet is associated with `athenaeum-public-security`.

---

## Infrastructure checkpoint

Before moving on to disk formatting and host setup, confirm all of the following.

### Compartment

- [ ] `Athenaeum`

### Networking

- [ ] `athenaeum-vcn` - `10.20.0.0/16`
- [ ] `athenaeum-igw` - enabled
- [ ] `athenaeum-public-routes` - `0.0.0.0/0 -> athenaeum-igw`
- [ ] `athenaeum-public-security`
- [ ] SSH 22 from your current `/32` only
- [ ] TCP 80 and 443 from `0.0.0.0/0`
- [ ] ICMP Type 3 / Code 4
- [ ] Egress all protocols to `0.0.0.0/0`
- [ ] `athenaeum-public` - regional public subnet, `10.20.1.0/24`

### Compute

- [ ] `athenaeum-arm`
- [ ] `VM.Standard.A1.Flex`
- [ ] 2 OCPUs / 12 GB RAM
- [ ] Ubuntu 26.04 LTS ARM64/aarch64
- [ ] 50 GB boot volume
- [ ] Dedicated Athenaeum SSH public key installed

### Public IP

- [ ] `athenaeum-public-ip`
- [ ] Reserved public IPv4
- [ ] Assigned to the VM's **primary private IP**
- [ ] Address recorded in private notes

### Persistent storage

- [ ] `athenaeum-data`
- [ ] Same availability domain as VM
- [ ] Intended custom size
- [ ] Balanced performance
- [ ] Paravirtualized, read/write attachment
- [ ] Status `Attached`

### Backups and IAM

- [ ] `athenaeum-backups` - private Standard bucket
- [ ] Versioning disabled
- [ ] Auto-tiering disabled
- [ ] Object Storage namespace recorded
- [ ] `AthenaeumBackupVMs` dynamic group
- [ ] `athenaeum-backup-objects` policy

---

## Continue with host setup

Proceed to [Guide 2 — Host setup](2-host-setup.md) to identify/mount the data disk, configure Ubuntu and Docker, install the command center, start the containers and prove backups. The tools use the OCI Python SDK with instance-principal authentication; a separate OCI CLI installation is not required.

## OCI reference links

- Always Free resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Compartments: https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingcompartments.htm
- VCNs: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingVCNs.htm
- Security rules: https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm
- Create an instance: https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm
- Reserved public IP assignment: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/reserved-public-ip-assign.htm
- Public IP management: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm
- Block volume creation: https://docs.oracle.com/en-us/iaas/Content/Block/Tasks/creatingavolume.htm
- Attach a block volume: https://docs.oracle.com/en-us/iaas/Content/Block/Tasks/attach-compute-volume-attachment.htm
- Object Storage buckets: https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/managingbuckets_topic-To_create_a_bucket.htm
- Dynamic groups: https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/To_create_a_dynamic_group.htm
- Dynamic-group rules: https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/Writing_Matching_Rules_to_Define_Dynamic_Groups.htm
- Instance principals: https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm
- IAM policy syntax: https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/policysyntax.htm
