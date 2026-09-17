# TutorBoard controlled pilot runbook

Scope: one authenticated teacher and one guest student on one standalone board.

## Release source

The frontend used by the board release pipeline is pinned independently from the vendored BoardDocument contract in `tutorboard-release.json`.

- `schemas/board/source.json` records BoardDocument contract provenance.
- `deploy/board-production/tutorboard-release.json` records the exact deployable TutorBoard commit.
- Never replace the deployment pin with `main`; releases must be reproducible from an immutable commit SHA.

When promoting a newer TutorBoard commit, update `tutorboard-release.json` in a pull request and require the complete `TutorBoard standalone release` workflow to pass. The cross-repository job must verify frontend format/lint/typecheck/tests, strict board build, and the two-client collaboration/reconnect/recovery E2E against the current backend.

## Pilot GO gate

The pilot is GO only when all of the following are true:

1. The board release workflow is green for the exact backend commit and pinned TutorBoard commit.
2. Images are identified by immutable registry digests.
3. The deployment is reachable through the real HTTPS hostname.
4. `/`, `/boards`, `/b/<boardId>`, `/j/<secret>`, Board API routes, and WebSocket collaboration pass smoke tests.
5. A teacher can create a board and issue an invitation.
6. A fresh guest browser can exchange the invitation and edit the same board without a user account.
7. Two-client live collaboration converges after reload and a temporary network interruption.
8. Read-only downgrade, write restoration, and revoke behave correctly; stale-epoch pending commands never return.
9. Confirmed board data survives Board API restart and full application-stack restart.
10. A backup is created off-host and an isolated restore drill succeeds.
11. Log-redaction smoke confirms invitation secrets and collaboration tickets are absent from persistent logs.
12. Rollback to the previous immutable release has been exercised on staging.
13. The staging deployment completes the soak gate without health, persistence, storage, or resource regressions.
14. Immediately before the first lesson, teacher and guest access are smoke-tested from two separate browser/device contexts using the production hostname.

## First-lesson change policy

After the final production smoke, freeze application changes until the lesson is complete. Prepare one board and its invitation in advance. Keep the previous release manifest and the most recent verified backup available for recovery.

## Required operator inputs

Infrastructure-specific values belong in deployment configuration/secrets and must stay out of Git:

- production hostname and DNS record;
- VPS or runner access;
- strong application/session secrets;
- PostgreSQL credentials;
- Redis configuration;
- S3-compatible storage credentials and backup destination;
- teacher bootstrap credentials;
- GitHub `board-staging` and `board-production` environment approvals/secrets where the workflow requires them.
