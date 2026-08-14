# Board envelope v1.5 validation

The server vendors the canonical `board/v1` artifacts from the TutorBoard
commit recorded in `schemas/board/source.json`. Generated Pydantic DTOs validate
BoardDocument `1.4`, BoardSnapshot `1.4`, and current ordered
BoardCommandEnvelope `1.5` at every HTTP and persistence boundary.

Envelope `1.5` adds required `originId`. The server enforces monotonic Lamport
ordering independently for each `(document, actor, origin)` tuple. This keeps
ordering deterministic while allowing multiple tabs or devices for the same
actor to submit independent command streams. The compatibility reader retains
flat envelopes `1.0`/`1.2` and ordered envelopes `1.3`/`1.4` for journal
recovery; new client writes use `1.5`.

The release gate covers:

- manifest and fixture SHA-256 verification;
- generated DTO freshness;
- required and bounded `originId` validation;
- monotonic Lamport validation per actor origin;
- mixed legacy/current command-journal recovery;
- deterministic conflict quarantine and independent-command progress;
- BoardDocument and BoardSnapshot `1.4` persistence;
- strict rejection of unknown fields, versions, and command kinds.

WebSocket live previews are deliberately outside this contract. They are
ephemeral protocol `1.1` events and never alter the canonical document without
a subsequent validated semantic command envelope.
