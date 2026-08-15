# TutorBoard REST API

The board API is session-authenticated and served from the same origin as
TutorBoard. It uses the vendored `board/v1` schemas and the persistent revision
journal described in `board-persistence.md`.

The runtime reader accepts flat envelopes `1.0` and `1.2`, ordered envelopes
`1.3` and `1.4`, and the current ordered envelope `1.5`. Version `1.5` adds a
durable browser `originId`; Lamport ordering is scoped to actor plus origin so
two tabs owned by the same person cannot invalidate one another's monotonic
clock. Version `1.4` remains the document/snapshot version and carries guided
3D learning state.

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
one-time ticket for the tenant/document-scoped WebSocket. Protocol `1.1`
carries revision notifications, a bounded presence roster, and ephemeral
`preview.ink` / `preview.transform` updates. Preview updates are rate-limited,
bounded, expire client-side, and are never written to the command journal or a
snapshot. A completed gesture is persisted only as its semantic command over
HTTP; clients recover through the normal pull/rebase API after reconnect.

Each socket validates same-origin access in production, consumes its ticket
once, enforces a 32 KiB message limit and 30 messages per second, and sends a
heartbeat interval in `ready`. The client coalesces revision signals, applies
exponential reconnect with jitter, clears stale participants/previews, and
pulls the authoritative command suffix before reporting itself recovered.

Commands that cannot be deterministically replayed are quarantined locally
instead of blocking independent pending commands. Evidence finalization is
disabled while the quarantine is non-empty, preventing a misleading
"successful" artifact from being finalized over unresolved local work.

Finalization at `POST /api/v1/boards/{document_id}/evidence` requires an
available snapshot at the exact revision and matching document SHA-256.
Manifest, SVG and optional PNG are immutable and verified on read. Student and
parent routes expose only explicitly published, non-revoked evidence.

GeometryOS browser traffic uses the authenticated same-origin
`/api/v1/geometryos/` gateway. Direct production browser access is unsupported.

## Standalone B0 contract

The routes above describe the currently wired lesson-bound runtime. The additive
standalone owner/guest contract is frozen separately in
`contracts/standalone-board/` and is intentionally not mounted by B0.

B0 adds strict Pydantic contract readers and shared fixtures for the future
`BoardAccessContext`, capability vocabulary, invitation management routes,
`cacheScopeId`, `accessEpoch`, and WebSocket access-control events. The matching
TutorBoard branch carries strict Zod readers for the same fixtures.

Runtime adoption is staged: T0 prepares the frontend security/persistence
boundary; B1 introduces standalone board ownership/persistence; B2 wires guest
invitation/session authorization. Until those PRs land, the existing context and
lesson-bound routes remain authoritative.
