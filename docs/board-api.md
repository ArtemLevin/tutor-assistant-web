# TutorBoard REST API

The board API is session-authenticated and served from the same origin as
TutorBoard. It uses the vendored `board/v1` schemas and the persistent revision
journal described in `board-persistence.md`.

The runtime reader accepts flat envelopes `1.0` and `1.2`, ordered envelopes
`1.3`, and the current ordered envelope `1.4`. Version `1.4` carries the
guided 3D learning commands and persists `solidLearningAttempts` inside the
canonical BoardDocument and BoardSnapshot.

## Access matrix

| Role | Read assigned board | Append commands | Save snapshots | Create/delete |
|---|---:|---:|---:|---:|
| `admin` | all tenant boards | yes | yes | yes |
| `tutor` | all tenant boards | yes | yes | yes |
| `student` | active `StudentAccess` only | yes | yes | no |
| `parent` | active `StudentAccess` only | no | no | no |

An authenticated user without access receives `404` for a board so the API
does not disclose whether a cross-student or cross-tenant identifier exists.
A parent attempting to change an assigned board receives `403`.

## Routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/boards/context` | actor, organization, role, and session CSRF token |
| `POST` | `/api/v1/lessons/{lesson_id}/board` | create or resolve the lesson board |
| `GET` | `/api/v1/boards/{document_id}` | latest valid snapshot plus command suffix |
| `GET` | `/api/v1/boards/{document_id}/commands` | command batches after `afterRevision` |
| `POST` | `/api/v1/boards/{document_id}/commands` | append one command envelope |
| `POST` | `/api/v1/boards/{document_id}/snapshots` | store one canonical snapshot |
| `GET` | `/api/v1/lessons/{lesson_id}/boards` | list active and archived lesson boards |
| `GET` | `/api/v1/boards/{document_id}/revisions` | list revision history |
| `POST` | `/api/v1/boards/{document_id}/archive` | archive without deleting evidence |
| `WS` | `/api/v1/boards/{document_id}/collaboration` | revision signals and ephemeral presence |
| `POST` | `/api/v1/boards/{document_id}/evidence` | finalize an exact immutable revision |
| `GET` | `/api/v1/lessons/{lesson_id}/board-evidence` | list role-visible evidence |
| `DELETE` | `/api/v1/boards/{document_id}` | soft-delete a board |

Unsafe requests require `X-CSRF-Token` from the context or board response.
They accept only `application/json`, except `DELETE`, and enforce the configured
command/snapshot size before parsing. The authenticated user ID must match the
envelope and every command `actorId`.

Successful board responses include `ETag`, `X-Board-Revision`, and
`X-CSRF-Token`. `baseRevision` is the authoritative optimistic-lock contract.
A stale append returns `409` with `currentRevision` and up to 500 missing
command batches so TutorBoard can deterministically rebase. `hasMore=true`
instructs the client to continue through the commands endpoint.

Rate limits use `RATE_LIMIT_BOARD_READS` and `RATE_LIMIT_BOARD_WRITES` in the
shared `RATE_LIMIT_WINDOW_SECONDS` window. Audit details contain identifiers,
revision counts, sizes, and digests; board command and snapshot content is not
copied into the audit log.
## Collaboration and evidence

The HTTP command log remains authoritative. `POST
/api/v1/boards/{document_id}/collaboration-ticket` returns a short-lived
one-time ticket for the tenant/document-scoped WebSocket. The socket carries
only revision notifications and ephemeral presence; clients recover through
the normal pull/rebase API.

Finalization at `POST /api/v1/boards/{document_id}/evidence` requires an
available snapshot at the exact revision and matching document SHA-256.
Manifest, SVG and optional PNG are immutable and verified on read. Student and
parent routes expose only explicitly published, non-revoked evidence.

GeometryOS browser traffic uses the authenticated same-origin
`/api/v1/geometryos/` gateway. Direct production browser access is unsupported.
