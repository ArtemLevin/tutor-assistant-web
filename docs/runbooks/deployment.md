# Runbook: production deployment

## Предусловия

- Ubuntu Server 22.04/24.04 LTS x86_64, подготовленный по
  [Ubuntu host runbook](ubuntu-host.md);
- private GHCR images и `docker login ghcr.io`;
- заполнены `.env.production`, `ALERT_WEBHOOK_URL` и файлы `deploy/production/secrets/*`;
- `GEOMETRYOS_IMAGE` содержит опубликованный образ GeometryOS с полным
  `@sha256:<64-hex-digest>`, а не только mutable tag;
- GitHub environments `staging` и `production`, для production назначены required reviewers;
- release tag опубликован и совпадает с release evidence; deploy фиксирует
  полученный registry digest в runtime-state.

Первичная подготовка: `make production-init`, затем заменить домены, provider
URL и пустые provider secrets. Backup endpoint обязан быть внешним HTTPS S3,
не `minio:9000`. Проверить `make production-config`; команда не должна печатать
значения секретов.

## Развёртывание

1. Pipeline выполняет lint, tests, PostgreSQL integration, security scan, migration check и сборку всех image targets.
2. На staging выполнить
   `make production-preflight RELEASE=v1.0.0-rc.1`, затем
   `make production-deploy RELEASE=v1.0.0-rc.1`.
3. Скрипт выполняет Ubuntu host preflight, преобразует backend и TutorBoard
   release tags в точные `repository@sha256:…`, проверяет и запускает внутренний
   GeometryOS по digest, инфраструктуру,
   backup, migration job, inactive blue/green slot и ждёт `/health/ready`.
4. Caddy атомарно переключается на новый slot; старый worker получает SIGTERM и до 90 секунд на graceful shutdown.
5. Выполняются smoke, 100-session load test и resilience drill.
6. Required reviewer подтверждает production environment; deployment повторно
   фиксирует фактический registry digest и сохраняет его для точного rollback.
7. После первого deployment/reboot выполняется
   `deploy/ubuntu/host-smoke.sh --verify-backup`.

Не запускать `alembic upgrade` из web-контейнера. Не использовать `latest`. После переключения проверить Grafana, queue age, dead-letter, delivery success и Caddy certificate events.
