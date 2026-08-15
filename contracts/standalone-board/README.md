# Standalone Board Contract v1

This directory freezes the cross-repository B0 contract shared by `tutorboard`
and `tutor-assistant-web`. It is additive: the existing lesson-bound Board API,
revision journal and command envelope `1.5` remain valid until later migration
PRs explicitly switch runtime composition.

## Contract version

`standalone-board/1.0`

The contract version is independent from the Board command envelope version.
The current command envelope remains `1.5`.

## Principal model

Two principals may enter a standalone board:

- `teacher`: an authenticated owner/admin/tutor account;
- `guest`: a board-scoped capability session created from an invitation.

Teacher authentication always wins if teacher and guest cookies coexist on the
same origin. Frontend code must render the principal returned by the context
endpoint and must not infer identity from URL shape.

## Capability vocabulary

- `board.read`
- `board.write`
- `board.snapshot.write`
- `collaboration.connect`
- `board.export`
- `board.history.read`
- `board.invites.manage`
- `board.archive`
- `board.delete`

`board.snapshot.write` is explicit in v1 because the current revision protocol
allows clients to persist canonical snapshots. A write-enabled guest may receive
it; a read-only guest may not. Snapshot persistence remains server-validated by
board, revision and digest. A future server-owned snapshotter may remove this
capability in a new contract version.

Unknown capabilities are rejected by strict client/server contract readers.
Backend authorization remains authoritative even when the frontend hides or
disables a control.

## BoardAccessContext

Every standalone bootstrap resolves a strict `BoardAccessContext` before the
sync engine starts. Required common fields:

- `schemaVersion`: `1.0`;
- `principalType`: `teacher` or `guest`;
- `actorId`: command actor bound to the principal;
- `boardId`: board allowed for this context;
- `role`: `admin`, `tutor` or `student`;
- `displayName`;
- `capabilities`;
- `csrfToken`;
- `cacheScopeId`: opaque local-persistence namespace, never a raw credential;
- `accessEpoch`: opaque permission epoch used to prevent stale offline writes.

Teacher contexts additionally carry `organizationId` and `userId`. Guest
contexts intentionally do not expose those account/tenant identifiers.

`cacheScopeId` is stable only while the security scope is valid. Two invitations
for the same board must have different guest scopes. Teacher and guest on the
same board must have different scopes.

`accessEpoch` changes whenever effective guest write authority changes, including
board-wide read-only, per-invitation write changes, rotation or revoke. Pending
commands created under an older epoch must never auto-push after reconnect.

## Invitation link contract

- Token entropy: at least 256 bits from a CSPRNG.
- Only an HMAC-SHA-256 digest keyed by a server-side pepper is persisted.
- The raw token is returned only by create/rotate responses.
- `GET /j/{secret}` exchanges the link for an `HttpOnly`, `Secure`,
  `SameSite=Lax` guest cookie and responds with `303` to `/b/{boardId}#/board`.
- The join response uses `Cache-Control: no-store`, `Referrer-Policy:
  no-referrer`, and `X-Robots-Tag: noindex, nofollow`.
- `/j/*` paths and WebSocket `ticket` query values are redacted from persistent
  logs, traces and error reporting.

Invitations remain reusable until expiry or revoke; this avoids link-preview
bots consuming one-time links.

## Guest session and CSRF

The guest cookie contains signed identifiers/version claims only; it never
contains the raw invitation token. Its lifetime cannot exceed the invitation.
Unsafe HTTP requests require a separate CSRF token from `BoardAccessContext`.
A credential-version mismatch invalidates the session.

## Access changes

WebSocket control events added by the standalone contract:

- `access.capabilities.changed`: refresh context before any pending push;
- `access.revoked`: terminal guest state; close with code `4403`; do not enter a
  reconnect loop.

Reconnect ordering is normative:

`online -> refresh context -> compare accessEpoch/capabilities -> pull -> rebase permitted pending -> push`.

Old-epoch pending commands are quarantined with reason
`access-epoch-changed`; restoring write permission does not resurrect them.

## Error vocabulary

The standalone public contract reserves:

- `board_not_found`
- `board_read_only`
- `board_deleted`
- `invitation_invalid`
- `invitation_expired`
- `invitation_revoked`
- `guest_session_invalid`
- `guest_session_version_mismatch`
- `board_revision_conflict`
- `board_lamport_conflict`
- `access_epoch_changed`
- `rate_limit_exceeded`

The public join page intentionally collapses invalid/expired/revoked invitation
reasons into one non-enumerating user-facing result.

## Legacy compatibility

B0 does not delete lesson-bound routes or alter existing responses. T0/B1 will
introduce dual readers and nullable lesson/student linkage. Runtime switching is
feature-gated until both repositories consume this contract.
