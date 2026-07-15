# Security hardening и observability

Документ описывает production-контур версии 0.11 и обязательные настройки эксплуатации.

## HTTP и сессии

- Cookie сессии подписана, имеет `HttpOnly`, а в production обязательно получает `Secure`;
  `SameSite` допускает только `lax` или `strict`.
- Абсолютный TTL задаётся `SESSION_MAX_AGE`, idle timeout — `SESSION_IDLE_TIMEOUT`, новый
  случайный session ID выпускается каждые `SESSION_ROTATION_SECONDS`.
- Все изменяющие HTML-form операции требуют session-bound CSRF token. После login и принятия
  приглашения старая сессия очищается.
- `TRUSTED_HOSTS` содержит только публичные host names. `TRUSTED_PROXY_IPS` содержит IP/CIDR
  ingress/reverse proxy, которому разрешено задавать forwarded scheme/client address. Значение
  `*` запрещено в production.
- Базовая CSP не разрешает inline script/style, внешние origins, object и framing. HTML preview
  пособия получает отдельную более строгую `sandbox` policy.
- Ответы включают CSP, HSTS на HTTPS-профиле, `nosniff`, deny framing, Referrer Policy,
  Permissions Policy, COOP и CORP.

В production TLS завершается на доверенном ingress, который перезаписывает, а не дополняет
полученные от клиента `X-Forwarded-*` headers. Прямой доступ к контейнеру приложения извне должен
быть закрыт. После смены `APP_SECRET_KEY` все действующие сессии становятся недействительными;
ротацию выполняют в согласованное окно или через двухэтапный deployment на уровне ingress/SSO.

## Rate limiting

Redis хранит счётчики в общем окне `RATE_LIMIT_WINDOW_SECONDS` для четырёх классов:

| Класс | Маршруты | Переменная |
|---|---|---|
| login | `POST /login` | `RATE_LIMIT_LOGIN` |
| invitations | создание, принятие и отзыв приглашений | `RATE_LIMIT_INVITATIONS` |
| callbacks | `/webhooks/*` | `RATE_LIMIT_CALLBACKS` |
| downloads | artifact/download/preview | `RATE_LIMIT_DOWNLOADS` |

После третьей login-попытки добавляется bounded delay; после лимита возвращается `429` и
`Retry-After`. При кратком отказе Redis действует локальный ограничитель процесса, а readiness
сигнализирует деградацию production-очереди.

## Секреты и production guardrails

Секреты принимаются из environment либо из файлов `*_FILE`. Файлы подходят для Docker secrets,
Kubernetes Secrets Store CSI, Vault Agent и облачных secrets managers; содержимое файла имеет
приоритет над одноимённым env-полем. Поддержаны:

- `APP_SECRET_KEY_FILE`, `DATABASE_URL_FILE`, `REDIS_URL_FILE`;
- `BBB_SECRET_FILE`, `BOOTSTRAP_ADMIN_PASSWORD_FILE`;
- `ARTIFACT_S3_SECRET_KEY_FILE`;
- `TRANSCRIPTION_WEBHOOK_TOKEN_FILE`, `MATERIALS_WEBHOOK_TOKEN_FILE`;
- `DOCUMENT_ENGINE_TOKEN_FILE`, `METRICS_BEARER_TOKEN_FILE`, `SENTRY_DSN_FILE`.

Production startup завершается ошибкой при SQLite, автоматических миграциях, HTTP public URL,
небезопасной cookie, wildcard hosts/proxies, eager workers, demo BBB/provider/passwords,
локальном artifact/document provider, выключенном ClamAV, JSON logs или metrics. Не храните
production `.env`, secret files и экспортированные конфигурации в Git.

## Логи, errors и correlation

`LOG_JSON=true` пишет однострочный JSON в stdout. Каждый HTTP request получает или валидирует
`X-Request-ID`; то же значение сохраняется в `processing_jobs` и `outbox_events`, передаётся в
Celery header и добавляется в логи/OTel span. Beat-задачи без входного ID получают новый UUID.

Formatter рекурсивно редактирует password/secret/token/cookie/transcript/notes/contact/content,
Bearer credentials, email, телефон и secret query parameters. Exception message/traceback не
пишется в stdout, потому что ответ внешнего провайдера может содержать текст занятия. Тип ошибки и
correlation ID остаются; полное событие отправляется только в опциональный Sentry с
`send_default_pii=false` и дополнительным scrubber. В production log sink также должен иметь
ограниченный RBAC и retention.

Audit log фиксирует скачивание и preview, публикацию, отзыв, создание/отзыв приглашения и изменение
membership/access. Audit записи tenant-scoped и не заменяют access log ingress/S3.

## Метрики и трассы

Prometheus endpoint `/metrics` использует Bearer token. Основные серии:

- HTTP count/latency;
- `tutor_workflow_duration_seconds{stage=transcription|generation|pdf_compilation|delivery}`;
- длительность занятия и размер артефакта;
- размер очереди и возраст самого старого элемента по очередям/outbox;
- readiness каждой зависимости и critical failure counter.

OpenTelemetry инструментирует FastAPI, HTTPX, SQLAlchemy и Celery. Compose направляет OTLP HTTP в
Collector, далее в Tempo. Grafana автоматически получает Prometheus и Tempo datasources и dashboard
`Tutor Assistant / Production`.

## Readiness и alerts

- `/health/live` подтверждает только работу процесса и подходит для liveness probe.
- `/health/ready` выполняет реальные проверки PostgreSQL, Redis, S3 и BBB с ограниченным timeout;
  при отказе обязательной зависимости возвращает `503`.
- Alert rules: dependency down, critical failures, queue age > 15 минут и HTTP 5xx rate > 5%.

Prometheus создаёт alert state, но для реальных уведомлений следует подключить Alertmanager к
email/Telegram/PagerDuty и задать route по `severity`. Минимальный runbook: найти correlation ID,
открыть trace в Tempo, проверить readiness/queue age, затем retry/cancel через `tutor-assistant-ops`.

## Проверка перед релизом

```bash
uv sync --extra dev
make check
curl -H "Authorization: Bearer $METRICS_BEARER_TOKEN" http://localhost:8000/metrics
curl http://localhost:8000/health/ready
```

CI отдельно выполняет Ruff, Bandit, production dependency export + `pip-audit`, unit tests и
PostgreSQL/Redis/MinIO integration tests. Critical/high security findings блокируют merge.
