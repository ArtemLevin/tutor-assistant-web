# Runbook: backup и restore drill

Backup стартует каждые 22 часа, оставляя запас для RPO 24 часа, и содержит PostgreSQL custom dump, копию private artifact bucket и JSON manifest с SHA-256 БД. При ошибке job повторяется через 15 минут. Bucket versioning и lifecycle дополняются явной очисткой наборов старше `BACKUP_RETENTION_DAYS`.

Для production задайте `BACKUP_S3_ENDPOINT_URL` с внешним HTTPS endpoint,
region, отдельный bucket и отдельные credentials на off-host AWS
S3/совместимое хранилище. Production preflight отклоняет встроенный MinIO,
localhost, незашифрованный endpoint, общий artifact/backup bucket и пустой
backup secret. Backup в том же host/volume не защищает от потери узла.

Ручной backup: `make production-backup`. Успех подтверждают manifest в `tutor-backups`, метрика `tutor_backup_last_success_timestamp_seconds` и отсутствие ошибок job.

Ежемесячная проверка:

1. Создать свежий backup с известным ID: `deploy/production/backup.sh --backup-id 20260715T120000Z`.
2. Запустить `make production-restore-drill BACKUP_ID=20260715T120000Z`.
3. Скрипт проверит SHA-256 dump, `pg_restore --list`, восстановит отдельную БД и private bucket, сравнит количество и checksum metadata артефактов.
4. Для ручной проверки PDF/HTML запустить с `KEEP_RESTORE_DRILL=true`; иначе isolated БД и bucket удаляются автоматически после проверок.
5. Записать длительность/RTO в release evidence и удалить сохранённый drill bucket командой `tutor-assistant-backup delete-drill`.

Restore в production требует отдельного change approval. Сначала изолированно подтвердить backup, остановить запись данных, сохранить forensic snapshot, затем восстановить PostgreSQL и S3. Никогда не направлять drill на текущую production БД или основной artifact bucket; CLI дополнительно требует `ALLOW_RESTORE=true`.


## F3.2.3: board media recovery gate

Restoring board-media-enabled PostgreSQL state requires the matching private S3
objects. Restore now accepts only an isolated PostgreSQL database named
`tutor_restore_*` and an isolated artifact bucket named `tutor-restore-*`,
in addition to `ALLOW_RESTORE=true`. The live app database, artifact bucket,
and backup bucket cannot be restore targets.

The restore operation first checks PostgreSQL dump integrity and copies the
artifact manifest. After copying, it queries every available, non-deleted
`board_media_assets` row in the restored database. Each corresponding S3 object
is read and independently checked for exact `byte_size` and
`content_sha256`. Successful output includes `verified_media_assets`.
A missing object, wrong byte length or wrong SHA-256 fails the restore gate;
matching S3 metadata alone is insufficient. Historical backups made before
board media migration are supported with zero verified media assets.

The board-production restore drill must contain the `verified_media_assets`
field before reporting success. Always preserve the live source and handle a
failed isolated drill as a release blocker. The complete backup of DB and
artifacts is not atomic across S3 and PostgreSQL; uploads and board writes
must be quiesced or covered by a verified point-in-time backup protocol
for production-grade restore consistency.

Integration regressions exercise PostgreSQL + MinIO with a deliberately
corrupted object whose S3 SHA metadata remains unchanged. Production
promotion stays closed until isolated restore, recovery and related
release checks succeed.
