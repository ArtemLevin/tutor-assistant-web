# ADR: Board-only runtime boundary

- Status: accepted for B0
- Date: 2026-08-15
- Scope: TutorBoard standalone v1 deployment

## Context

TutorBoard requires a production backend, but the mature Board persistence,
revision journal, collaboration broker and identity infrastructure already live
in `tutor-assistant-web`. Extracting a new backend repository before the guest
access model stabilizes would duplicate security- and durability-critical code.
At the same time, deploying the entire tutor-assistant product would expose
unrelated services and operational dependencies.

## Decision

The first standalone release keeps Board API implementation in
`tutor-assistant-web` and adds a composition profile named:

```text
APP_PROFILE=board
```

The profile may mount only the runtime needed by standalone TutorBoard:

```text
identity + boards + guest-access + audit + health + metrics
```

It must exclude classroom/student catalog, scheduling UI, portal, BBB,
transcription, materials/document generation and unrelated workers. GeometryOS
is optional and must be explicitly enabled/pinned.

The deployment publishes a board-specific immutable image:

```text
ghcr.io/artemlevin/tutorboard-api:<release>
```

The public topology remains same-origin from the browser perspective:

```text
Caddy
  /, /b/*        -> TutorBoard UI
  /api/*, /j/*   -> Board API
  websocket      -> Board API collaboration endpoint
```

PostgreSQL is authoritative for durable board state/command journal. Redis is
used for collaboration tickets, presence, Pub/Sub and rate limits. Object
Storage holds snapshots/backups. Redis loss may interrupt live collaboration but
must not lose accepted commands.

## Protocol boundary

B0 does not change the Board command protocol. Envelope `1.5`, sequential server
revisions, SHA-256 validation, idempotency, Lamport ordering, HTTP push/pull,
snapshots and one-time WebSocket tickets remain the interoperability baseline.
Standalone work changes ownership/access/bootstrap, not synchronization
semantics.

`BoardSyncEngine` will stop creating lesson boards in T0. Board creation belongs
to management/API routes. Legacy lesson-bound routes remain available during the
additive migration until traffic and rollback requirements allow removal.

## Security boundary

Caddy and Board API must redact `/j/{secret}` path values and WebSocket `ticket`
query values before persistent logging/tracing. Production WebSockets require a
valid same-origin `Origin` and one-time ticket. Board API is authoritative for
all capabilities; the UI is not a security boundary.

## Repository boundary

Cross-repository contract files under `contracts/standalone-board/` are the B0
compatibility gate. `tutorboard` consumes the frontend contract; the Board API
implements the server contract. Changes that alter capability names, context
shape, access epoch semantics or error vocabulary require coordinated contract
versioning.

A separate Board API repository is deferred until after production
stabilization and requires a new ADR backed by measured operational benefits.

## Consequences

Positive:

- reuses proven persistence/collaboration code;
- minimizes deployment attack surface compared with full tutor-assistant;
- permits incremental migration with rollback;
- keeps browser HTTP/WS same-origin.

Costs:

- two repositories must coordinate contract changes;
- `tutor-assistant-web` needs a strict composition root and board-only CI image;
- temporary legacy/standalone dual-read compatibility remains until cleanup.

## Rejected alternatives

- **Deploy full tutor-assistant stack:** unnecessary attack surface and runtime
  dependencies for the board-only product.
- **Immediately extract a new backend repository:** duplicates unstable access
  logic and complicates rollback.
- **Move collaboration authority to the browser:** violates durability and
  revoke requirements.
