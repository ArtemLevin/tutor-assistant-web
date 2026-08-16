# B1 — Standalone board persistence/model

Status: implementation decision for the standalone TutorBoard rollout.

## Scope

B1 adds durable teacher-owned boards to the existing Board API without enabling
invitation exchange or guest sessions. The existing lesson-bound board protocol,
revision journal, snapshots, collaboration transport and command envelope `1.5`
remain unchanged.

B2 owns `board_invitations`, raw-token exchange, guest cookies, guest principals
and capability derivation. D1 owns `APP_PROFILE=board`.

## Schema expansion

Migration `0015_standalone_board_persistence` expands `board_documents` with:

- `owner_user_id` — authenticated owner for standalone rows;
- `title` — standalone display title;
- `guest_writes_enabled` — board-wide write switch, default `true`;
- `access_version` — monotonically increasing permission/state version, default `1`;
- nullable `lesson_id` and `student_id` for standalone rows.

The target architecture describes `owner_user_id` and `title` as required. During
the transition they remain SQL-nullable because historical lesson-bound rows do
not have trustworthy creator metadata to backfill. Database checks enforce the
stronger invariant for every standalone row:

```text
lesson_id IS NULL AND student_id IS NULL
    => owner_user_id IS NOT NULL
       AND title IS NOT NULL
       AND trim(title) <> ''
```

Half-linked rows are forbidden: `lesson_id` and `student_id` are either both set
(legacy) or both null (standalone).

Ownership is tenant-bound at the database level by a composite foreign key from
`(organization_id, owner_user_id)` to the existing unique membership key
`memberships(organization_id, user_id)`. A standalone board therefore cannot be
persisted with an owner from another organization.

## Ownership and authorization

B1 preserves legacy authorization and adds the following standalone rules:

- `admin`: may read/write/manage any board in the organization;
- `tutor`: may read/write/manage a standalone board only when
  `owner_user_id == principal.user_id`;
- a non-owner tutor receives `404`, not `403`, to avoid board-ID enumeration;
- `student` and `parent` account principals cannot access standalone boards in
  B1; guest access is introduced only by B2;
- `GET /api/v1/boards` is owner-scoped even for administrators because the B0
  contract defines it as the authenticated teacher's board list.

Existing lesson-bound tutor/admin/student/parent behavior is retained.

## Standalone management API

B1 implements the B0 management subset:

```text
POST   /api/v1/boards
GET    /api/v1/boards
PATCH  /api/v1/boards/{boardId}
POST   /api/v1/boards/{boardId}/archive
POST   /api/v1/boards/{boardId}/unarchive
DELETE /api/v1/boards/{boardId}
```

`POST /api/v1/boards` generates the public board id server-side. An omitted title
becomes `Новая доска`. The response is the strict B0 `StandaloneBoardDescriptor`.

`PATCH` accepts `title` and/or `guestWritesEnabled`. Empty patches and
`title: null` are rejected.

The existing recovery/sync endpoints are not rewritten in B1. T1 will connect
the `/b/<boardId>` context-first frontend to the already-persisted standalone
row after B2 provides principal/capability context.

## `access_version`

`access_version` is server-internal in B1 and is not added to the B0 public board
descriptor. It increments when a change can alter effective guest access:

- `guest_writes_enabled` changes value;
- board is archived;
- board is unarchived;
- board is soft-deleted.

Renaming a board and idempotently setting the same guest-write value do not bump
the version. B2 will combine this board version with invitation credential/access
versions to derive opaque `accessEpoch` values.

## Rollback policy

`0015` is an expand migration. Rolling the application back while leaving the
schema at `0015` is supported and is the preferred production rollback because
legacy code ignores the additive columns.

A physical Alembic downgrade to `0014` is allowed only while no standalone row
exists. Once standalone data exists, downgrade raises an explicit error rather
than deleting or inventing lesson/student linkage. Before a later contract
migration can make standalone ownership columns globally `NOT NULL`, legacy rows
must first be retired or receive a separately approved ownership migration.

## B1 gates

B1 is complete only when:

- fresh migration to `0015` succeeds on SQLite and PostgreSQL CI;
- pre-use downgrade `0015 -> 0014` succeeds;
- downgrade with standalone data fails closed without data loss;
- create/list/update/archive/unarchive/delete standalone API tests pass;
- owner-scoped list and non-owner tutor `404` isolation pass;
- legacy lesson-bound board creation and descriptor remain compatible;
- all existing board persistence, collaboration, security and production tests remain green.
