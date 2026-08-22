# TutorBoard standalone production runbook

## 1. Подготовка инфраструктуры

Нужны Linux host с Docker Compose v2, домен с A/AAAA записью, открытые 80/TCP, 443/TCP и 443/UDP, OCI registry и отдельный off-host S3 bucket для backup. Для staging используйте другой host либо отдельные project name, DNS, credentials, buckets и volumes.

```sh
deploy/board-production/init.sh
cp deploy/board-production/.env.production.example deploy/board-production/.env.production
```

Заполните `.env.production`. Значения `BACKEND_IMAGE_REPOSITORY` и `TUTORBOARD_IMAGE_REPOSITORY` указываются без tag. Файлы в `deploy/board-production/secrets/` создаются с режимом `0600`; замените `backup_s3_secret_key` значением off-host S3.

Проверьте соответствие access key:

- `ARTIFACT_S3_ACCESS_KEY` — пользователь локального MinIO;
- `minio_root_user` и `minio_root_password` — bootstrap MinIO;
- `BACKUP_S3_ACCESS_KEY` и `backup_s3_secret_key` — отдельный off-host principal;
- artifact и backup buckets имеют разные имена.

## 2. Первый выпуск

Получите digest опубликованного tag:

```sh
set -a
. deploy/board-production/.env.production
set +a
deploy/board-production/resolve-release.sh <git-sha-or-release-tag> \
  > deploy/board-production/runtime/resolved.env
. deploy/board-production/runtime/resolved.env
```

Запустите миграцию и inactive slot:

```sh
deploy/board-production/deploy.sh \
  "$BOARD_API_DIGEST" \
  "$TUTORBOARD_DIGEST" \
  "$MIGRATION_DIGEST" \
  "$OPS_DIGEST"
```

Скрипт поднимает PostgreSQL/Redis/MinIO, создаёт private artifact bucket, выполняет migration, проверяет API/UI, меняет upstream Caddy и записывает `runtime/release-manifest.json`.

## 3. Создание доски для ученика

1. Откройте `https://<домен>/login` и войдите bootstrap admin.
2. Откройте `https://<домен>/boards`.
3. Создайте доску и invitation с именем ученика.
4. Включите или отключите право записи.
5. Скопируйте URL вида `https://<домен>/j/<secret>` и отправьте ученику по доверенному каналу.
6. Ученик открывает ссылку. Backend меняет secret на HttpOnly guest session и перенаправляет на `/b/<board-id>#/board`.

Invitation secret показывается только при создании или rotate. Он не должен попадать в тикеты, снимки экрана и журналы поддержки.

## 4. Ежедневные проверки

```sh
deploy/board-production/smoke.sh
deploy/board-production/backup.sh
```

Контролируйте `/health/ready`, защищённый `/metrics`, Caddy 5xx, заполнение дисков, restart count, PostgreSQL connections, Redis memory и возраст последнего backup. Рекомендуемые alerts:

- readiness отсутствует 2 минуты;
- HTTP 5xx > 1% за 5 минут;
- p95 API latency > 750 ms за 10 минут;
- любой unexpected container restart;
- backup старше 26 часов;
- disk > 80%, PostgreSQL volume > 75%;
- WebSocket abnormal closure rate > 5% за 10 минут.

## 5. Backup и restore drill

```sh
backup_id=$(date -u +%Y%m%dT%H%M%SZ)
deploy/board-production/backup.sh --backup-id "$backup_id"
deploy/board-production/restore-drill.sh "$backup_id"
```

Drill восстанавливает PostgreSQL и artifacts в изолированные ресурсы. Production database и artifact bucket не изменяются.

## 6. Rollback приложения

```sh
deploy/board-production/rollback.sh
```

Rollback использует digest предыдущего slot и не выполняет Alembic downgrade. Если миграция несовместима с предыдущим кодом, остановите promotion и следуйте миграционному incident plan: восстановление backup в новый stateful stack, проверка, затем переключение DNS/Caddy.

## 7. Компрометация invitation

1. В управлении доской нажмите revoke либо rotate.
2. Убедитесь, что активный ученик получил `access.revoked`, а WebSocket закрылся с `4403`.
3. При rotate передайте новую ссылку отдельно.
4. Проверьте Caddy logs sentinel-тестом:

```sh
CHECK_LOG_REDACTION=true deploy/board-production/smoke.sh
```

## 8. Полная остановка

Перед обслуживанием создайте backup. Остановите stateless slot и Caddy через Compose. Stateful volumes удалять запрещено. Операции `docker compose down -v`, ручное удаление named volumes и очистка S3 выполняются только по отдельному change request с проверенным backup.
