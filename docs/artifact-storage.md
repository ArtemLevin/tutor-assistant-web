# S3/MinIO artifact storage

Production uses a private S3 bucket for generated PDF, TEX, HTML and lesson media. Object keys
start with the organization id, so tenant separation is preserved in storage as well as in
PostgreSQL queries. Downloads are streamed only after application authorization; the application
does not issue public object URLs.

## Configuration

Set `ARTIFACT_STORAGE_PROVIDER=s3`, `ARTIFACT_S3_BUCKET`, region and credentials. For MinIO also
set `ARTIFACT_S3_ENDPOINT_URL`. AWS S3 uses the standard credential chain when access and secret
keys are empty. Production validation requires S3 and ClamAV.

Initialize policy and lifecycle rules:

```bash
uv run tutor-assistant-artifacts configure-bucket
```

Uploads are streamed through a bounded spool, checked for maximum size, MIME signature and
SHA-256, then scanned through the ClamAV `INSTREAM` protocol. Objects use SSE-S3 (`AES256`). A
database row moves through `uploading`, `available`, `quarantined`, and `deleted`. Failed uploads
remain `uploading` for diagnosis and never become downloadable.

`ARTIFACT_RETENTION_DAYS` controls soft expiry. `ARTIFACT_DELETE_GRACE_DAYS` is the recovery
window before physical deletion. S3 lifecycle provides a final expiry guard after retention plus
the recovery window. AWS S3 lifecycle and the maintenance worker clean incomplete multipart
uploads; MinIO uses the worker because its lifecycle API does not support that action. The age is
set by `ARTIFACT_ABORT_MULTIPART_DAYS`. Celery beat verifies object bytes against the stored SHA-256 and
size, quarantines mismatches, expires retained objects, and purges due soft deletes.

## Migration from local storage

Keep `ARTIFACT_STORAGE_ROOT` pointed at the old directory, configure S3 as the active provider,
then run:

```bash
uv run tutor-assistant-artifacts migrate-local --dry-run
uv run tutor-assistant-artifacts migrate-local --limit 500
uv run tutor-assistant-artifacts verify --limit 500
```

The migration supplies the existing database SHA-256 as the expected checksum. A mismatch aborts
the object upload. Re-running is safe: objects with the expected checksum are skipped.

Operators can revoke bytes without immediate destruction:

```bash
uv run tutor-assistant-artifacts soft-delete ARTIFACT_ID --organization ORGANIZATION_ID
```

## Backup restore check

Restore into a separate private bucket, point a maintenance container at that bucket, and run
`tutor-assistant-artifacts verify`. Promote the restored bucket only when the command reports zero
quarantined objects. CI performs the same copy-and-check sequence against two MinIO buckets.
