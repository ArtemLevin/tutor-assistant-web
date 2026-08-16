# B2 security regression matrix

The B2 merge gate covers the standalone guest-access invariants below in addition to the repository's existing full test and production-release suites.

| Area | Regression gate |
| --- | --- |
| Secret storage | Raw invitation secrets are absent from database rows and invitation list responses; only HMAC-SHA-256 digests persist. |
| Join exchange | Valid links issue a separate HttpOnly/Lax guest session and redirect with no-store/no-referrer/noindex headers. |
| Enumeration resistance | Invalid, expired and revoked public links share the same public response. |
| Principal precedence | An authenticated teacher session wins when a guest cookie is also present. |
| Least privilege | Guest context contains no tenant/account identifiers and never receives management/history/export capabilities. |
| Cache isolation | Separate invitations to the same board receive distinct cache scopes and actor identities. |
| Write authority | Board-wide and per-invitation write switches remove write/snapshot capabilities. |
| Stale offline writes | Durable guest mutations require the current access epoch; an old epoch is rejected before persistence. |
| Credential rotation | Revoke/rotate invalidates old guest cookies and collaboration tickets through credential-version mismatch. |
| Collaboration | Tickets are board/client/principal bound; capability changes refresh access and revoke is terminal with close code 4403. |
| Observability | `/j/*` secrets and `ticket=` values are redacted before logging/Sentry/OpenTelemetry persistence. |
| Abuse control | Join and invitation management share the invitation rate-limit bucket and use the frozen problem code. |
| Migration safety | 0016 can downgrade while unused and fails closed once invitation metadata exists. |
| Legacy compatibility | Lesson-bound board behavior and command envelope `1.5` remain unchanged. |
