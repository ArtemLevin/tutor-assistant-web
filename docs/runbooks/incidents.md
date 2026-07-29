# Runbook: критические зависимости

| Сбой | Немедленное действие | Восстановление |
|---|---|---|
| PostgreSQL | остановить scheduler и mutating traffic, не удалять volumes | failover/restore, readiness, сверка outbox и jobs |
| Redis | сохранить web read-only доступ; Celery retries не обходить вручную | поднять Redis AOF, scheduler переопубликует durable outbox |
| S3/MinIO | запретить download/publication, не помечать job completed | восстановить bucket, integrity scan, повторить delivery |
| BBB | занятия перевести на резервную ссылку, callbacks сохранять идемпотентно | readiness, replay callback только после проверки receipt |
| transcription/document API | circuit breaker и bounded retries, наблюдать queue age | manual retry dead-letter после восстановления provider |
| TutorBoard WebSocket | сохранить HTTP push/pull; presence считать недоступным, но не потерей данных | проверить Redis pub/sub, one-time tickets и Origin; клиенты перечитают command log |
| GeometryOS gateway | отключить prompt UI, ручное полотно и sync оставить доступными | проверить upstream readiness и correlation ID, не разрешать браузерный обход gateway |
| board evidence | запретить publish при quarantine/S3 ошибке, snapshot и manifest не удалять | integrity scan, повтор deterministic upload, сверка SHA-256 |
| утечка секрета | P1, отозвать secret и активные sessions | ротация, audit review, incident report |

Для P1 назначить incident commander, сохранить correlation IDs и временную шкалу, каждые 30 минут обновлять статус. Не помещать транскрипты, телефоны или secrets в тикет/чат. После восстановления провести RCA в течение двух рабочих дней и добавить проверку, которая предотвращает повторение.
