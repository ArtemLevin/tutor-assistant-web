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
