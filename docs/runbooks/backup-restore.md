# Runbook: backup и restore drill

Backup стартует каждые 22 часа, оставляя запас для RPO 24 часа, и содержит PostgreSQL custom dump, копию private artifact bucket и JSON manifest с SHA-256 БД. При ошибке job повторяется через 15 минут. Bucket versioning и lifecycle дополняются явной очисткой наборов старше `BACKUP_RETENTION_DAYS`.

Для production задайте `BACKUP_S3_ENDPOINT_URL`, region и отдельные credentials на off-host AWS S3/совместимое хранилище. Встроенный MinIO default предназначен для самодостаточного staging; backup в том же host/volume не защищает от потери узла.

Ручной backup: `make production-backup`. Успех подтверждают manifest в `tutor-backups`, метрика `tutor_backup_last_success_timestamp_seconds` и отсутствие ошибок job.

Ежемесячная проверка:

1. Создать свежий backup с известным ID: `deploy/production/backup.sh --backup-id 20260715T120000Z`.
2. Запустить `make production-restore-drill BACKUP_ID=20260715T120000Z`.
3. Скрипт проверит SHA-256 dump, `pg_restore --list`, восстановит отдельную БД и private bucket, сравнит количество и checksum metadata артефактов.
4. Для ручной проверки PDF/HTML запустить с `KEEP_RESTORE_DRILL=true`; иначе isolated БД и bucket удаляются автоматически после проверок.
5. Записать длительность/RTO в release evidence и удалить сохранённый drill bucket командой `tutor-assistant-backup delete-drill`.

Restore в production требует отдельного change approval. Сначала изолированно подтвердить backup, остановить запись данных, сохранить forensic snapshot, затем восстановить PostgreSQL и S3. Никогда не направлять drill на текущую production БД или основной artifact bucket; CLI дополнительно требует `ALLOW_RESTORE=true`.
