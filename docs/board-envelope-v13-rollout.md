# Board command compatibility

## Supported envelopes

| Envelope | Read | Write | Ordering metadata | Persisted Lamport range |
| --- | --- | --- | --- | --- |
| `1.0` | Yes | Historical clients only | None | `NULL / NULL` |
| `1.2` | Yes | Historical clients only | None | `NULL / NULL` |
| `1.3` | Yes | Current TutorBoard | `baseRevisionAtCreation` and actor-local `lamport` | Positive `lamport_min / lamport_max` |

The server preserves the canonical payload in its original envelope version. Mixed
journals are replayed by ascending server revision. Lamport values establish the
order of one actor's commands within one board; server revision remains the total
order across actors.

## Command compatibility

| Capability | Command kind | TutorBoard reader | Server reader |
| --- | --- | --- | --- |
| Base board editing | Existing `core.*` command set | `0.1.0+` | `board/v1` |
| Atomic Smart Ink acceptance | `core.objects.replace` | Current release | `board/v1` with replace support |

`core.objects.replace` carries complete original and replacement snapshots.
Older strict readers reject this command explicitly. Shared-board deployments
must update the server reader before enabling Smart Ink writes.

## Acceptance invariants

1. Runtime schema validation completes before persistence.
2. The authenticated principal, envelope actor and every nested command actor match.
3. An idempotency retry with the same canonical payload returns the committed batch.
4. Reuse of an idempotency key with a different payload returns `409`.
5. Envelope `1.3` Lamport values are positive and strictly increasing.
6. The first incoming Lamport value is greater than the latest committed value for
   the same organization, board and actor.
7. `baseRevisionAtCreation` is at most the envelope base revision.
8. Client timestamps are retained for audit and display. Acceptance order is based
   on server revision and Lamport metadata.

## Deployment sequence

1. Back up PostgreSQL and verify restore tooling.
2. Deploy the server version that reads envelopes `1.0`, `1.2` and `1.3`.
3. Apply migration `0013_board_ordering`.
4. Verify readiness, migration head and a synthetic envelope `1.3` append/read cycle.
5. Replay a mixed `1.0`/`1.2`/`1.3` journal and compare the resulting document SHA-256.
6. Deploy the finalized TutorBoard commit recorded in `schemas/board/source.json`.
7. Confirm Chromium and Firefox sync smoke tests, then enable shared-board traffic.

## Rollback

Before the first envelope `1.3` write, both services can return to the preceding
release. After `1.3` batches exist, keep a server reader capable of replaying them.
During an incident, disable `VITE_FEATURE_SERVER_SYNC` or redeploy the preceding
TutorBoard build while the current server continues mixed-version reads. Migration
`0013` remains in place; legacy rows retain `NULL / NULL` Lamport ranges.

## Operational signals

Monitor these Board sync events and structured fields:

- `envelope_1.0_received`, `envelope_1.2_received`, `envelope_1.3_received`;
- `lamport_conflict`, `revision_conflict`, `idempotent_retry`;
- document ID, actor ID, schema version, base revision, committed revision,
  Lamport range, command count and payload SHA-256.

A rise in Lamport conflicts indicates actor-clock reuse or corrupted local queue
state. A rise in revision conflicts without successful retries indicates an
incomplete journal or client recovery problem.
