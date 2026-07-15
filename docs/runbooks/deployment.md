# Runbook: production deployment

## Предусловия

- Linux host с Docker Engine, Compose v2, DNS на host и открытыми 80/443;
- private GHCR images и `docker login ghcr.io`;
- заполнены `.env.production`, `ALERT_WEBHOOK_URL` и файлы `deploy/production/secrets/*`;
- GitHub environments `staging` и `production`, для production назначены required reviewers;
- image tag неизменяем и совпадает с release tag.

Первичная подготовка: `make production-init`, затем заменить домены, provider URL и пустые provider secrets. Проверить `make production-config`; команда не должна печатать значения секретов.

## Развёртывание

1. Pipeline выполняет lint, tests, PostgreSQL integration, security scan, migration check и сборку всех image targets.
2. На staging: `make production-deploy RELEASE=v1.0.0-rc.1`.
3. Скрипт запускает инфраструктуру, backup, migration job, inactive blue/green slot и ждёт `/health/ready`.
4. Caddy атомарно переключается на новый slot; старый worker получает SIGTERM и до 90 секунд на graceful shutdown.
5. Выполняются smoke, 100-session load test и resilience drill.
6. Required reviewer подтверждает production environment; выполняется тот же immutable image tag.

Не запускать `alembic upgrade` из web-контейнера. Не использовать `latest`. После переключения проверить Grafana, queue age, dead-letter, delivery success и Caddy certificate events.
