# Yandex Cloud production provisioning

This directory provisions the existing single-host production topology on an
Ubuntu 24.04 VM. It deliberately keeps responsibilities separated:

1. Terraform creates network, restricted security group, static IPv4, VM,
   service account, optional DNS record and the Lockbox access binding.
2. Minimal cloud-init creates `tutor-deploy`, installs Python/Git and records
   the Lockbox secret ID. It does not clone code, install Docker or receive a
   secret payload.
3. Ansible checks out an exact approved commit, invokes the existing hardened
   Ubuntu bootstrap, materializes Lockbox payload directly on the VM and runs
   the existing preflight/deployment scripts.

Terraform never reads a Lockbox payload, so secret values are not written to
Terraform state. Store state in a protected remote backend and restrict access
to the operators who can create or replace production infrastructure.

Official references:

- [Yandex Cloud Terraform provider](https://yandex.cloud/en/docs/tutorials/infrastructure-management/terraform-quickstart)
- [Compute Cloud VM metadata](https://yandex.cloud/en/docs/compute/concepts/vm-metadata)
- [VM access to Lockbox](https://yandex.cloud/en/docs/compute/operations/vm-create/create-with-lockbox-secret)
- [Security groups](https://yandex.cloud/en/docs/vpc/operations/security-group-create)

## Prerequisites

- active Yandex Cloud billing and an existing folder;
- an existing public DNS zone, or permission to create the A record elsewhere;
- Terraform 1.8+, Ansible Core 2.16+ and an authenticated Yandex provider;
- a dedicated off-host Object Storage bucket for backups and a least-privilege
  static access key;
- published backend, TutorBoard and GeometryOS images;
- one Lockbox secret containing all keys listed below.

Production defaults create paid resources: 8 vCPU, 32 GiB RAM, a 200 GiB
network SSD, a static public IPv4 address and one Lockbox access binding. The
VM and reserved address have deletion protection enabled by default; disabling
it requires an explicit reviewed Terraform variable change.

## Lockbox payload

Create the secret outside Terraform so values cannot enter `.tfstate`. Add one
non-empty, single-line text entry for every key:

```text
app_secret_key
artifact_s3_secret_key
backup_s3_secret_key
bbb_secret
bootstrap_admin_password
document_engine_token
ghcr_token
grafana_admin_password
materials_webhook_token
metrics_bearer_token
minio_root_password
postgres_password
redis_password
sentry_dsn
transcription_webhook_token
```

The VM service account gets `lockbox.payloadViewer` only on this secret. The
materializer obtains a short-lived IAM token from the Compute metadata service,
downloads the payload over the Lockbox API, writes files with mode `0600`, and
does not print values. Rotate a value by adding a new Lockbox version and rerun:

```bash
sudo /usr/local/sbin/tutorboard-materialize-lockbox
```

Then restart or redeploy the affected services. Existing containers keep their
current Docker secret mount until recreated.

## Terraform

```bash
cd deploy/yandex-cloud/terraform
cp terraform.tfvars.example terraform.tfvars
# Fill real IDs, the operator public key and a restricted SSH /32 or VPN CIDR.
terraform init
terraform fmt -check
terraform validate
terraform plan -out production.tfplan
terraform apply production.tfplan
```

`ssh_allowed_cidrs` rejects `0.0.0.0/0`. TCP 80/443 and UDP 443 are public;
all database, Redis, MinIO and observability ports remain inside Docker
networks and are not published by the security group.

If `dns_zone_id` is omitted, create the A record manually from the `public_ip`
output before Ansible preflight. Wait for the domain to resolve to that address.

## Ansible and first deployment

Run Ansible from the approved checkout that contains the same infrastructure
code. Do not point it at an unreviewed branch.

```bash
cd deploy/yandex-cloud/ansible
cp inventory.example.yml inventory.yml
cp vars.example.yml vars.yml
# Use terraform output public_ip and fill every non-secret deployment value.
ansible-playbook playbook.yml -e @vars.yml
```

The safe first pass uses `perform_deploy: false`: it provisions, fetches
Lockbox, authenticates GHCR, initializes production files and runs preflight.
After DNS, external providers, image release identifiers and backup credentials
are verified, set `perform_deploy: true` and rerun the same playbook.

The deploy script pulls the approved backend and TutorBoard tags once, resolves
their registry `RepoDigests`, and records only `repository@sha256:…` references
for the active application slot, workers and one-shot jobs. Runtime preflight
rejects a tag-only active image. Rollback reuses the exact stored digests and
refuses to resolve a potentially moved tag.

The production host continues to use the canonical checkout at
`/opt/tutorboard-stack`, systemd, the existing blue-green deploy script, smoke
checks, rollback and backup/restore runbooks.

## Required post-deploy gates

```bash
ssh tutor-deploy@$(terraform -chdir=../terraform output -raw public_ip)
cd /opt/tutorboard-stack
deploy/ubuntu/host-smoke.sh --verify-backup
```

Then perform the documented reboot drill and verify that the stack returns to
healthy state without manual container intervention. Enable Yandex Cloud budget
alerts, Audit Trails and VM/disk monitoring before production approval.
