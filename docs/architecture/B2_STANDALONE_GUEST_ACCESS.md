# B2 — Standalone guest invitations and access

Status: implemented behind the standalone-board API surface. This document refines the B0 contract and B1 persistence decisions without changing Board command envelope `1.5`.

## Scope

B2 adds the backend capability needed for a teacher to create an exclusive browser link for a standalone board and for a student to open that board without an account.

Implemented surfaces:

- durable invitation metadata;
- transient secret-bearing join URLs;
- `/j/{secret}` exchange into a board-scoped guest session;
- strict guest `BoardAccessContext`;
- guest read, write, snapshot and collaboration authorization;
- invitation update, revoke and rotation;
- board-wide and invitation-specific write authority changes;
- access epoch enforcement for durable guest mutations;
- terminal collaboration revocation;
- secret-bearing URL/query redaction;
- rate limiting and non-enumerating invalid-link responses.

B2 deliberately does **not** add the standalone frontend route/list/invitation UI. Those remain T1/T2.

## Security principal model

A guest is not represented as a normal Tutor Assistant account. `GuestPrincipal` is an internal board-scoped capability principal and is never persisted as a `users` row.

The public guest context contains no organization or account identifiers. The server retains the organization id internally only to address the correct board partition.

If a normal authenticated session and a guest cookie coexist, the normal authenticated principal is resolved first. This prevents a latent guest cookie from downgrading or confusing a teacher session.

## Invitation secret handling

Join secrets are created with `secrets.token_urlsafe(32)`, giving 256 bits of CSPRNG input entropy.

The database stores only:

`HMAC-SHA-256(invitation_pepper, raw_secret)`

The invitation pepper is a domain-separated key derived from `APP_SECRET_KEY`; the raw secret is returned only by create/rotate responses and is never returned by list/update endpoints.

Invitation links remain reusable until expiration or revocation. Link preview software therefore cannot consume a one-time invitation accidentally.

## Guest session

`/j/{secret}` verifies the digest and creates a separate signed guest cookie containing only:

- guest-session format version;
- invitation id;
- board id;
- invitation credential version;
- opaque per-session guest actor id.

The raw invitation secret is never copied into the cookie.

Cookie properties:

- HttpOnly;
- SameSite=Lax;
- Path=/;
- Secure whenever the application secure-cookie setting is enabled; production configuration already requires secure session cookies;
- maximum lifetime is bounded by both the configured guest-session maximum and the invitation expiry.

The join response is `303` to `/b/{boardId}#/board` and carries `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and `X-Robots-Tag: noindex, nofollow`.

Invalid, expired and revoked links collapse into the same public `invitation_invalid` response.

## Capability derivation

Every usable guest receives:

- `board.read`;
- `collaboration.connect`.

A guest receives `board.write` and `board.snapshot.write` only when all of these are true:

1. invitation `write_enabled=true`;
2. board `guest_writes_enabled=true`;
3. board is not archived/deleted;
4. invitation is not revoked.

A guest never receives export, history, invitation management, archive or delete capabilities.

## Cache scope and access epoch

`cacheScopeId` is an opaque HMAC namespace derived from the invitation id and credential version. Two invitations to the same board therefore cannot share the same durable browser queue. Rotation also receives a new scope.

`accessEpoch` is an opaque HMAC over:

- board id;
- board access version;
- invitation id;
- invitation access version;
- invitation credential version.

Board access version changes on board-wide guest-write changes, archive/unarchive and delete. Invitation access version changes on invitation write/expiry changes. Credential version changes on rotate/revoke.

Every unsafe guest board mutation requires both:

- `X-CSRF-Token` from the current guest context;
- `X-Board-Access-Epoch` equal to the current context epoch.

This makes an old offline write fail with `access_epoch_changed` even if write permission is later restored.

## Credential invalidation

`credential_version` is monotonic. Revoke and rotate increment it. Existing guest cookies and one-time WebSocket tickets embed the previous version and therefore fail revalidation immediately.

Rotation creates a new raw secret/digest and reactivates a previously revoked invitation. The previous raw secret becomes unusable.

## Collaboration control plane

Guest collaboration tickets contain board/client/principal binding plus invitation id and credential version. The server revalidates the invitation before accepting the socket.

Permission changes publish invitation-targeted control messages through the existing collaboration broker. Internal routing fields are stripped before serialization to the browser.

Public control events remain the strict B0 shapes:

- `access.capabilities.changed` — client must refresh context before further mutation;
- `access.revoked` — terminal event followed by WebSocket close code `4403`.

Read-only guests may remain connected for presence and revision notifications, but preview mutation messages are rejected.

## Observability and abuse controls

Before persistent logging/Sentry processing, `/j/{secret}` path segments are replaced by `/j/[REDACTED]`. Query parameters named `ticket`, `token`, `secret`, `password` or `checksum` are redacted.

The OpenTelemetry request hook also overwrites secret-bearing URL/path/query attributes with redacted values.

Public join and invitation-management endpoints use the invitation rate-limit bucket. Standalone rate-limit failures use the frozen `rate_limit_exceeded` problem code.

## Persistence and rollback

Alembic `0016_board_guest_invites` creates `board_invitations` with composite board tenancy, digest uniqueness, monotonic version checks, expiry/revocation/use metadata and board-cascade deletion.

Application rollback may safely keep schema 0016. Physical downgrade to 0015 is allowed only while the invitation table is empty; once invitation metadata exists it fails closed rather than silently deleting access state.

## Compatibility

B2 does not rewrite lesson-bound board creation, sync journal semantics, evidence/history authorization or command envelope `1.5`.

`GET /api/v1/boards/context` preserves the legacy authenticated response when called without a board id. Standalone teacher context is selected with `boardId`, while a guest session resolves its own single board context. This bridge remains until T0/T1 move the frontend completely to the strict standalone launch contract.
