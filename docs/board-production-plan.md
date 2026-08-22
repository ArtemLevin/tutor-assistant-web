# План завершения TutorBoard standalone: D1–D4 и backend B3

## Цель

Развернуть TutorBoard как самостоятельный сервис для преподавателя и гостя-ученика с отдельным профилем backend, минимальным набором зависимостей, межпроцессной совместной работой, изолированными данными и управляемым blue/green-релизом.

## Статус реализации

| Этап | Результат | Проверка |
|---|---|---|
| D1 | `APP_PROFILE=board`, минимальный контейнер, строгий HTTP/WS allowlist, отдельные Compose и Caddy, readiness PostgreSQL/Redis/S3 | `tests/test_board_profile.py` |
| B3 | Redis Pub/Sub, распределённые одноразовые tickets и presence, targeted capability/revocation events, закрытие гостевого WS `4403` | `tests/test_redis_integration.py`, `tests/test_board_collaboration.py` |
| D2 | Отдельный workflow backend/frontend, profile contract, PG/Redis/S3, two-client E2E, SBOM, Trivy, hardening, staging и protected production | `.github/workflows/board-release.yml` |
| D3 | Отдельный Compose project и volumes, restart drill, backup/isolated restore, 24-часовой soak | `deploy/board-production/` |
| D4 | Два слота API/UI, атомарная смена Caddy upstream, manifest с digest, возврат на предыдущий слот | `deploy.sh`, `rollback.sh` |

Внешние staging/production-гейты требуют подготовленных self-hosted runners, DNS, TLS, registry и S3. До их успешного прохождения production promotion остаётся закрыт средой GitHub `board-production`.

## Контракт D1

- `APP_PROFILE` принимает `full` и `board`; значение по умолчанию — `full`.
- `ENABLED_MODULES` при профиле `board` вызывает ошибку конфигурации.
- Контейнер board создаёт Database, WebSupport, IdentityService, AuditService factory, BoardPersistenceService factory, BoardGuestAccessService, S3 ArtifactStorage и CollaborationBroker.
- Профиль не создаёт conference, materials, transcription, jobs, DocumentEngine и ClamAV scanner.
- OpenAPI UI, static backend, lessons, students, classroom, materials, portal, evidence, revisions и GeometryOS отсутствуют.
- Production readiness проверяет только PostgreSQL, Redis и S3.

## Контракт B3

1. Каждый процесс API подключается к одному Redis namespace.
2. Collaboration ticket хранится в Redis с TTL и атомарно потребляется через `GETDEL`.
3. Room events передаются через tenant- и board-bound канал с SHA-256 именем.
4. Изменение `writeEnabled` публикует targeted `access.capabilities.changed`.
5. Revoke, rotate и delete публикуют targeted или room-wide `access.revoked`.
6. WebSocket удаляет внутренние поля `_canWrite` и `_targetInvitationId` перед отправкой.
7. После revocation соединение закрывается кодом `4403`; stale HTTP mutation отклоняется по access epoch.

## Release gates D2

Обязательная последовательность:

1. Backend format, lint, tests, Bandit, dependency audit и secret scan.
2. Route/provider profile contract.
3. PostgreSQL migrations, MinIO и два экземпляра Redis broker.
4. Frontend format, lint, types, tests и board-profile build.
5. Playwright two-client collaboration/reconnect/recovery.
6. Backend `web`, `migration`, `ops` и frontend image build.
7. SBOM и Trivy HIGH/CRITICAL gate, non-root check.
8. Compose render и Caddy validation.
9. Staging deployment, smoke, secret-log sentinel, restart drill, backup/restore и 24-hour soak.
10. Protected approval и production blue/green rollout.

## Acceptance D3

- Staging использует собственный `BOARD_COMPOSE_PROJECT_NAME`, DNS, buckets, credentials и named volumes.
- У всех stateful services отсутствуют host ports.
- PostgreSQL, Redis и MinIO переживают restart drill; UI/API восстанавливают readiness.
- Restore создаёт временные `tutor_restore_*` database и `tutor-restore-*` bucket, проверяет Alembic и artifact hashes, затем очищает их.
- Soak длится минимум 86400 секунд, выполняет smoke каждые 300 секунд и допускает 0 неожиданных рестартов по умолчанию.

## Acceptance D4

- Release принимает только полные repository digest `@sha256:<64 hex>`.
- Migration выполняется один раз до переключения трафика.
- Inactive slot проходит readiness перед Caddy reload.
- Release manifest фиксирует timestamp, Git commit, slot и четыре image digest.
- Rollback запускает предыдущий slot, валидирует Caddy, переключает upstream, выполняет smoke и останавливает проблемный slot.
- Downgrade схемы выполняется отдельной одобренной процедурой после проверки совместимости.
