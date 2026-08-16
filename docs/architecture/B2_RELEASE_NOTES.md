# B2 release notes

B2 introduces account-free, board-scoped guest access for standalone TutorBoard documents while keeping existing lesson-bound synchronization compatible.

The release is gated on strict invitation-secret handling, credential rotation and revocation, CSRF plus access-epoch enforcement, terminal WebSocket revoke semantics, privacy-safe URL redaction, PostgreSQL migration compatibility, legacy collaboration E2E, and production image/security checks.
