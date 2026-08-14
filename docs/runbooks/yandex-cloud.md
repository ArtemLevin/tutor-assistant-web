# Runbook: Yandex Cloud production

The canonical provisioning assets live in
[`deploy/yandex-cloud`](../../deploy/yandex-cloud/README.md). This runbook covers
the operational sequence after those assets have been reviewed.

## Provisioning gate

1. Review the saved Terraform plan: one non-preemptible Ubuntu VM, one static
   public IP, restricted SSH, TCP 80/443, UDP 443, service account and a
   secret-scoped `lockbox.payloadViewer` binding.
2. Confirm Terraform state is remote, encrypted and access-controlled. It must
   not contain Lockbox payloads, static access secret keys or GHCR tokens.
3. Apply, publish the DNS A record and wait until the application domain
   resolves to the Terraform `public_ip` output.
4. Run Ansible with `perform_deploy: false`; archive the preflight output.
5. Verify release identifiers and the full GeometryOS `@sha256:` digest, then
   rerun with `perform_deploy: true`. Confirm `runtime/deployment.env` contains
   full digests for the active web, worker, TutorBoard, scheduler, migration and
   ops images.

Infrastructure creation is chargeable. Never run `terraform apply` or
`terraform destroy` from an automated pull-request check.

## Lockbox rotation

Add a new Lockbox version, then on the VM run:

```bash
sudo /usr/local/sbin/tutorboard-materialize-lockbox
cd /opt/tutorboard-stack
deploy/production/init.sh
sudo systemctl restart tutorboard-stack.service
deploy/ubuntu/host-smoke.sh
```

For database, Redis or MinIO password rotation, schedule a maintenance window:
those credentials also protect stateful containers and require a coordinated
service/data migration rather than only recreating application containers.

## Deployment and rollback

```bash
cd /opt/tutorboard-stack
git fetch origin <approved-commit>
git switch --detach <approved-commit>
deploy/ubuntu/preflight.sh <backend-release> <tutorboard-release>
deploy/production/deploy.sh <backend-release> <tutorboard-release>
deploy/ubuntu/host-smoke.sh --verify-backup
```

On a failed smoke, the deploy script returns traffic and deployment state to
the previous slot. If an operator rollback is required, follow
[`rollback.md`](rollback.md); it reuses the stored image digests and refuses a
tag-only fallback. Do not delete volumes or destroy the VM.

## Reboot and recovery drill

After first deployment and host-level changes:

```bash
sudo reboot
# reconnect after Compute reports RUNNING
systemctl is-active tutorboard-stack.service
cd /opt/tutorboard-stack
deploy/ubuntu/host-smoke.sh --verify-backup
sudo journalctl -u tutorboard-stack.service -b --no-pager
```

Record DNS/HTTPS recovery time, `/health/ready`, a successful two-client board
session, latest off-host backup ID and the journal excerpt in release evidence.

## Terraform changes

Use `terraform plan` for routine drift review. VM replacement destroys the
local named volumes with the boot disk; restore from a verified off-host backup
before switching DNS. A production `terraform destroy` always requires a
separate explicit approval and a recent restore drill.
