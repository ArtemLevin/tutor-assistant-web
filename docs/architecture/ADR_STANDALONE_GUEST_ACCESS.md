# ADR: Standalone guest capability-link access

- Status: accepted for B0
- Date: 2026-08-15
- Scope: TutorBoard standalone v1
- Contract: `contracts/standalone-board/`

## Context

TutorBoard must allow a teacher-owned board to be opened by a student without a
student account. Existing synchronization is mature and identity is currently
lesson/account-oriented. The new access model must not weaken tenant isolation,
command actor validation, CSRF, offline durability or live collaboration.

## Decision

A teacher creates a reusable invitation containing at least 256 bits of CSPRNG
entropy. The server stores only an HMAC-SHA-256 digest keyed by a server-side
pepper. `GET /j/{secret}` validates the invitation and exchanges it for a signed,
board-scoped guest session. The raw invitation token is never copied into the
session, database, Redis, audit records, metrics, traces or persistent logs.

The guest session resolves to a strict `BoardAccessContext`. Authorization is
capability-based and backend-authoritative. Guest capabilities may include
`board.read`, `board.write`, `board.snapshot.write` and
`collaboration.connect`; management/history/export capabilities are forbidden.

`board.snapshot.write` is retained in v1 because current synchronization can
persist canonical client snapshots. It is available only while `board.write` is
effective and remains constrained by board scope, revision and document digest.
This avoids rewriting the established revision protocol in B0/T0.

## Session precedence

V1 keeps one public origin. If a valid teacher session and guest session coexist,
the teacher principal wins. The frontend must trust the context response rather
than URL shape. A separate guest hostname is a documented fallback if staging
reveals session-confusion that cannot be eliminated with deterministic
precedence.

## Durable local state

Guest credentials are never stored in JS-readable storage. The server returns an
opaque `cacheScopeId`; durable heads, pending commands, clocks, sequences,
quarantine and recovery metadata will be scoped by `(cacheScopeId, boardId)` in
T0. Different invitations and teacher/guest principals must never share a local
security scope.

The server also returns an opaque `accessEpoch`. Effective write-authority
changes advance the epoch. A reconnect must refresh context before uploading
pending commands. Commands created under an older epoch are quarantined as
`access-epoch-changed` and are not automatically resurrected if write access is
later restored.

## Live access changes

Two control events are reserved:

- `access.capabilities.changed`: client refreshes context before any further
  mutation/pending push;
- `access.revoked`: terminal guest state; active socket closes with `4403`; the
  client must not reconnect-loop.

HTTP write and WebSocket ticket issuance independently re-check current server
authority, so stale UI cannot bypass read-only/revoke.

## Link and logging privacy

Invitation links are reusable until expiry/revoke to tolerate messenger preview
bots. Join responses use `Cache-Control: no-store`, `Referrer-Policy:
no-referrer`, and `X-Robots-Tag: noindex, nofollow`. `/j/*` path secrets and the
WebSocket `ticket` query parameter must be redacted from Caddy logs, application
logs, Sentry and tracing before production.

Invalid/expired/revoked invitation states may be distinguished internally for
metrics but collapse into one public non-enumerating error experience.

## Consequences

Positive:

- no student registration flow;
- board-scoped least privilege;
- no CRDT/revision rewrite;
- immediate server-authoritative revoke/read-only;
- offline safety is explicit rather than inferred from UI state.

Costs:

- possession of the link is possession of access until expiry/revoke;
- frontend persistence needs a schema migration in T0;
- Board API needs invitation/session/access-version persistence in B1/B2;
- multi-principal same-origin behavior requires dedicated E2E coverage.

## Rejected alternatives

- **Anonymous boards:** rejected because ownership, audit and revoke become
  unreliable.
- **One-time invitation consumption:** rejected because preview bots can consume
  legitimate links.
- **Raw token storage:** rejected because database/log compromise would reveal
  live invitation credentials.
- **Frontend-only read-only:** rejected because stale/offline clients could write.
- **CRDT rewrite:** rejected because it does not solve access control and would
  replace a working revision protocol during a security-sensitive migration.
