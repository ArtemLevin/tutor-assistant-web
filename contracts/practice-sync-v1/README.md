# practice-sync-v1

Versioned wire contract shared with `ArtemLevin/students-26-27`.

## Endpoints

- `GET /api/v1/practice/me/bootstrap` — authenticated student bootstrap; returns canonical snapshot when one exists.
- `GET /api/v1/practice/me/state` — canonical PracticeState v2.
- `POST /api/v1/practice/me/events:batch` — bounded immutable event ingestion; duplicate `eventId` delivery is idempotent.
- `PUT /api/v1/practice/me/state` — optimistic snapshot update using `baseRevision`; stale writes return HTTP 409 with the canonical state.

Mutations use the existing same-origin session and `X-CSRF-Token`. No long-lived credential belongs in the public student repository.

`PracticeState` remains local-first. `revision` is server-owned after binding. Events are immutable and use stable `eventId` values. Client timestamps are learning metadata; canonical ordering is based on server revision/receipt.

The fixture `fixtures/sync-cycle.json` is intentionally duplicated byte-for-byte in both repositories and is covered by contract tests.
