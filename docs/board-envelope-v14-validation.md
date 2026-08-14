# Board envelope v1.4 validation (historical)

This document records the previous compatibility gate. The current writer and
ordering contract is documented in
[`board-envelope-v15-validation.md`](board-envelope-v15-validation.md).

The server vendors the canonical `board/v1` artifacts from TutorBoard commit
`19f4f845727a49407454a0468e47a8ffe548709c`. Generated Pydantic DTOs validate
BoardDocument, BoardSnapshot, and ordered BoardCommandEnvelope version `1.4`
at every HTTP and persistence boundary.

The compatibility reader keeps flat envelopes `1.0` and `1.2` and ordered
envelopes `1.3` recoverable. It rejects guided 3D learning commands when they
are mislabeled as version `1.3`; those commands require envelope `1.4`.

The release gate covers:

- manifest and fixture SHA-256 verification;
- generated DTO freshness;
- ordered Lamport and base-revision validation for versions `1.3` and `1.4`;
- mixed legacy/current command-journal recovery;
- BoardDocument and BoardSnapshot `1.4` persistence;
- strict rejection of unknown fields, versions, and command kinds.
