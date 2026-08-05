# TutorBoard board contract v1

This directory is the machine-readable boundary between TutorBoard and the
server that persists and synchronizes boards. JSON uses TutorBoard's native
camelCase field names. Every schema is self-contained and targets JSON Schema
2020-12.

## Artifacts

- `BoardDocument 1.1` is the canonical persisted board state.
- `BoardCommandEnvelope 1.1` carries one atomic, idempotent command batch
  against a known base revision.
- `BoardSnapshot 1.1` binds a canonical document to a server revision and
  SHA-256 digest.
- `BoardGeometryImport 1.1` records GeometryOS GIR/Layout provenance without
  adding transport state to `BoardDocument`.

The manifest hashes every schema and canonical fixture. Run
`npm run board-contract:check` to verify freshness and executable validation.
The supported command matrix is recorded in `COMPATIBILITY.md`.

## Compatibility policy

- Additive: optional fields or new non-breaking metadata may be added in a
  minor contract revision after both consumers accept them.
- Minor: new command kinds or object kinds require fixtures, tolerant-reader
  evidence, and an explicit supported-version matrix.
- Breaking: removing or renaming fields, changing meanings or bounds, making an
  optional field required, or changing canonicalization requires a new major
  contract directory.

Unknown major versions and unknown persistent command/object kinds must be
rejected explicitly. Consumers must never silently discard unknown data.
