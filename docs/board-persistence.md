# TutorBoard persistence

The `boards` module owns server-side TutorBoard durability. Its authenticated
REST boundary and role policy are documented in `board-api.md`.

## Stored state

- `board_documents` links one board to one organization, student, and lesson.
- `board_command_batches` stores an ordered `BoardCommandEnvelope 1.0` journal.
- `board_snapshots` stores metadata for canonical snapshot JSON in artifact
  storage (S3/MinIO in production).
- `board_geometry_imports` stores GeometryOS version and digest provenance. The
  source prompt is represented only by SHA-256 in this operational table.

Composite foreign keys enforce organization, student, lesson, and board
ownership in the database. A board identifier is unique inside an
organization, not globally.

## Revision and idempotency rules

Each accepted command envelope advances the board by one server revision. The
write holds a row lock, compares `baseRevision` with `current_revision`, and
then atomically persists the batch and new document digest.

`idempotencyKey` is unique per board. Repeating an identical request returns
the previously assigned revision. Reusing the key with different canonical
JSON is a conflict. The check is repeated after acquiring the row lock so
concurrent retries remain idempotent.

## Snapshot rules

The service verifies:

1. `documentId` matches the embedded document.
2. Canonical document SHA-256 matches `documentSha256`.
3. The digest matches the command journal at the requested revision.
4. The snapshot is within configured size limits.

Canonical JSON follows TutorBoard's key-sorted JSON representation, including
UTC timestamps with millisecond precision. Snapshot objects are stored under:

```text
{organization}/boards/{document}/snapshots/{revision}-{sha256}.json
```

The database stores both the document digest and the whole-object digest.
Recovery loads the newest valid snapshot and returns command batches after its
revision.

Uploads use a two-phase `uploading → available` state. No database row lock is
held during the S3 request. A failed upload keeps its deterministic key and
metadata in `uploading` with a bounded error message, so the same request can
retry without creating a second snapshot row. Integrity failures quarantine
the snapshot.

Default compaction thresholds are 100 command batches or 5 MiB since the last
snapshot. `BOARD_SNAPSHOT_INTERVAL_COMMANDS` and
`BOARD_SNAPSHOT_INTERVAL_MB` tune these thresholds.

## Retention

Soft deletion marks the board and its snapshots with a grace-period deadline.
Purge removes snapshot objects first, then deletes database state with
cascading command and provenance rows. Production board deployments require
S3-compatible artifact storage. The existing maintenance worker applies board
retention, purges due boards, and verifies snapshot size and SHA-256. Bucket
lifecycle is configured from the longer of material and board retention
windows so MinIO/S3 cannot remove a live snapshot early.


## F3.2.2 — media upload reliability and unreferenced inventory

Board media binary bytes live in private artifact storage; the PostgreSQL
\`board_media_assets\` table carries status and immutable metadata. During an
upload, the service locks the board row for the short reservation/finalization
transactions and releases the lock during the S3/MinIO transfer. The backend
rechecks write authority immediately before finalization. Reusing the same
idempotency key for an identical available upload returns the original asset;
a different payload with that key is rejected.

**Compensating delete:** if storage reports failure after writing bytes (e.g.
an acknowledgement is lost), the service attempts to delete the upload-unique
storage key before marking metadata deleted or quarantined. Cleanup failure is
logged as \`board.media.cleanup_failed\` and still requires operational repair:
there is no distributed transaction between S3 and PostgreSQL.

\`BoardMediaService.unreferenced_report(document_id)\` provides per-board,
tenant-scoped counts and bytes for \`uploading\` and \`available\` records whose
\`first_referenced_revision\` is null. This detects abandoned reservations and
available assets that are not yet referenced by the command journal. It is a
read-only inventory; it must not delete assets, including available uploads
whose corresponding command may still be delayed in an offline queue. Physical
cleanup of unreferenced available media needs an explicit retention/restore
policy and a conservative delayed-command horizon.

Verification:
- \`tests/test_board_media.py\`: post-persist provider failure, retry,
  unreferenced/ref-marked inventory, rejected authorization.
- \`tests/test_board_media_postgres_minio.py\`: actual PostgreSQL row-lock
  quota concurrency, private MinIO reads, idempotency, revoke-before-finalize
  and post-write storage failure recovery.
- \`make test\`; the CI PostgreSQL integration job runs the combined
  PostgreSQL/MinIO reliability suite when both services are available.

Known follow-up: durable cleanup/reconciliation of stranded
\`uploading\` rows and S3 objects after process termination, and a
retention-aware policy for unreferenced \`available\` rows. Disable
production media uploads until these are operationally gated.
