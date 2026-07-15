# Tutor Assistant Web · BigBlueButton pilot

Быстрый пилот единого рабочего пространства репетитора: ученики, недельное расписание,
виртуальный класс BigBlueButton и фоновая подготовка материалов после занятия.

Пилот можно запустить без BigBlueButton. В `BBB_DEMO_MODE=true` доступны все административные
сценарии, подписанная ссылка ученика и демонстрационный экран класса. После подключения реального
BBB ссылки ведут в полноценную комнату с видео, аудио, записью и многопользовательской доской.

## Что уже работает

- карточки учеников: класс, предмет, цель, контакты родителя, ставка, ссылки и заметки;
- недельное расписание с локальным часовым поясом и проверкой пересечений;
- snapshot ставки в каждом занятии;
- отдельные роли преподавателя и ученика в BigBlueButton;
- случайные идентификаторы и пароли комнаты;
- HMAC-подписанная публичная ссылка ученика;
- включение записи с уведомлением о согласии участников;
- завершение комнаты преподавателем;
- синхронизация метаданных записей через `getRecordings`;
- Celery-задача обработки занятия;
- универсальный webhook генерации материалов;
- локальные Markdown-черновики, если AI webhook ещё не настроен;
- большой редактор сводных заметок;
- тёмный адаптивный интерфейс;
- health checks, Docker Compose, Makefile, uv и CI.
- модульный composition root и явный реестр функций;
- заменяемые провайдеры конференций, материалов и фоновых заданий;
- возможность включать только выбранные модули через `ENABLED_MODULES`.
- организации, пользователи и membership-роли `admin`, `tutor`, `student`, `parent`;
- Argon2-хеширование паролей и подписанные пользовательские сессии;
- обязательная tenant-изоляция CRM, расписания, комнат, jobs и материалов;
- управляемые миграции Alembic и автоматический upgrade пилотной базы без потери данных.
- управление участниками и ролями организации через административный интерфейс;
- одноразовые приглашения с ограниченным сроком действия;
- переключение между доступными рабочими пространствами;
- tenant-scoped журнал аудита административных и бизнес-операций.
- подписанный `recording-ready` callback BigBlueButton с защитой от повторной доставки;
- transactional outbox, Celery Beat и экспоненциальные повторные попытки;
- локальная транскрибация через `faster-whisper` либо заменяемый transcription webhook;
- сегменты транскрипта, редактирование текста и включение расшифровки в evidence JSON;
- наблюдаемые этапы post-lesson workflow и идемпотентное сохранение материалов.
- `LessonEvidenceBundle v1` с JSON Schema и SHA-256 снимком входных данных;
- заменяемые `DocumentEngine` и `ArtifactStorage`;
- версионированные TEX, HTML и PDF с журналом сборки;
- отдельные стадии проверки, согласования и публикации;
- адаптер компиляции к реальному API `latex-for-everyone`.
- ролевые приглашения ученика и родителя из карточки ученика;
- личный кабинет с опубликованными PDF/HTML/TEX;
- атомарная публикация и отзыв через transactional outbox;
- tenant-scoped доставки и внутренние уведомления.
- PostgreSQL production profile с настраиваемым pool и SQL timeout;
- tenant foreign keys и конкурентный outbox на `FOR UPDATE SKIP LOCKED`;
- отдельный migration job и проверяемый перенос существующей SQLite-базы.
- durable Celery workers с Redis AOF, late acknowledgement и graceful shutdown;
- отдельные очереди `transcription`, `materials`, `delivery`, `maintenance`;
- lease/heartbeat, автоматическое восстановление зависших jobs и dead-letter;
- bounded retry с exponential backoff/jitter и circuit breaker внешних сервисов;
- экран «Задачи» и CLI для retry, отмены и повторной отправки outbox.
- secure sessions, CSRF, строгий CSP, TrustedHost/proxy policy и распределённые rate limits;
- JSON-логи без PII, сквозной correlation ID для HTTP/outbox/Celery и audit скачиваний;
- OpenTelemetry → Tempo, Prometheus-метрики, Grafana dashboard, readiness и базовые alerts;
- Bandit и `pip-audit` в обязательном CI security pipeline.
- production images для web/worker/scheduler/migration/ops, non-root runtime и immutable tags;
- TLS reverse proxy Caddy, blue/green deployment, практический rollback и отдельные migration jobs;
- автоматический PostgreSQL+S3 backup, checksum manifest, retention и изолированный restore drill;
- SLO/error budget, release load gates, эксплуатационные runbooks и approval-gated v1.0.0.

## Быстрый старт в demo-режиме

Требования: Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --extra dev
uv run tutor-assistant-web
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --extra dev
uv run tutor-assistant-web
```

Откройте <http://localhost:8000>. Используйте `BOOTSTRAP_ADMIN_EMAIL` и
`BOOTSTRAP_ADMIN_PASSWORD` из `.env`. Эти значения создают первого администратора при пустой базе.
Следующие запуски сохраняют существующий пароль; изменение переменной не сбрасывает учётную запись.

Demo-данные создаются один раз при пустой базе. Для чистой базы установите `SEED_DEMO_DATA=false`.

## Команды

```bash
make sync       # зависимости
make migrate    # применить миграции базы
make run        # web-приложение
make worker     # Celery worker
make worker-transcription # worker транскрибации
make worker-materials     # worker генерации
make worker-delivery      # worker доставки
make worker-maintenance   # outbox и восстановление lease
make beat       # планировщик transactional outbox
make outbox     # однократная отправка накопленных событий
make sync-transcription # зависимости локального faster-whisper
make check      # Ruff + pytest
make security   # Bandit + аудит зависимостей
make test-postgres # интеграционные тесты PostgreSQL
make schema-check # проверить контракт LessonEvidenceBundle v1
make diagnose   # безопасная диагностика конфигурации
make docker-up  # app + worker + PostgreSQL + Redis
```

Операторские команды:

```bash
uv run tutor-assistant-ops list --organization <organization-id>
uv run tutor-assistant-ops retry <job-id> --organization <organization-id>
uv run tutor-assistant-ops cancel <job-id> --organization <organization-id>
uv run tutor-assistant-ops resend-outbox <event-id> --organization <organization-id>
uv run tutor-assistant-ops recover --limit 100
```

Если Windows Make сообщает, что `uv` не найден, проверьте `uv --version` в том же PowerShell и
передайте полный путь:

```powershell
make sync UV="$env:USERPROFILE\.local\bin\uv.exe"
```

Прямые команды `uv ...` остаются основным и кроссплатформенным способом запуска.

## Подключение BigBlueButton

BigBlueButton следует разместить на отдельном сервере в соответствии с его
[официальной инструкцией](https://docs.bigbluebutton.org/administration/install/). Он требует домен,
TLS, доступные WebRTC-порты и поддерживаемую Ubuntu.

На BBB-сервере получите URL и shared secret:

```bash
sudo bbb-conf --secret
```

Заполните `.env`:

```dotenv
BBB_DEMO_MODE=false
BBB_BASE_URL=https://class.example.com
BBB_SECRET=long-shared-secret
TRANSCRIPTION_PROVIDER=faster-whisper
PUBLIC_BASE_URL=https://tutor.example.com
APP_ENV=production
APP_SECRET_KEY=a-unique-random-application-secret-over-32-characters
APP_RELOAD=false
DATABASE_URL=postgresql+psycopg://tutor:strong-password@postgres:5432/tutor
AUTO_MIGRATE=false
TRUSTED_HOSTS=tutor.example.com
TRUSTED_PROXY_IPS=10.0.0.10
BOOTSTRAP_ADMIN_EMAIL=tutor@example.com
BOOTSTRAP_ADMIN_PASSWORD=a-long-unique-admin-password
SESSION_COOKIE_SECURE=true
SEED_DEMO_DATA=false
LOG_JSON=true
METRICS_ENABLED=true
METRICS_BEARER_TOKEN=a-unique-random-metrics-token-over-24-characters
DOCUMENT_ENGINE_PROVIDER=latex-for-everyone
DOCUMENT_ENGINE_URL=https://latex.example.com
DOCUMENT_ENGINE_TOKEN=service-access-token
```

Приложение использует официальный checksum API:

- `create` — идемпотентно создаёт комнату;
- `join` — выдаёт роли `MODERATOR` и `VIEWER`;
- `end` — завершает встречу;
- `getRecordings` — получает готовые записи.

Shared secret остаётся только на backend. В браузер передаются уже подписанные join URL.

## Docker Compose

Создайте `.env`, затем выполните:

```bash
docker compose up --build
```

Compose запускает приложение, четыре специализированных Celery worker, Celery Beat, PostgreSQL и
Redis с AOF, MinIO, ClamAV, Prometheus, Grafana, Tempo и OpenTelemetry Collector. Сам
BigBlueButton подключается как внешний сервис. Одноразовый сервис `migrate` применяет
Alembic-ревизии до запуска web и workers. Перед запуском задайте в `.env` уникальные
`POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` и
`METRICS_BEARER_TOKEN`; Compose больше не подставляет demo-пароли.

Для локального запуска без Redis оставьте `TASK_EAGER=true`: обработка выполнится в процессе web.
Для Compose и production используется `TASK_EAGER=false`.

## Production release

Базовая production-топология — `compose.production.yml`; Helm намеренно не добавлен, пока не
выбрана Kubernetes-инфраструктура. Приложение разворачивается blue/green за Caddy, а web, worker,
scheduler и migration job используют отдельные image targets.

```bash
make production-init
# заполнить deploy/production/.env.production и пустые provider secrets
make production-config
make production-deploy RELEASE=v1.0.0
```

`deploy.sh` принимает только immutable tag, делает backup, запускает migration отдельным job,
ждёт readiness неактивного slot и лишь затем переключает Caddy. `make production-rollback`
возвращает предыдущий application release. Schema rollback требует отдельного подтверждения и
проверенного Alembic downgrade.

Workflow `Production release` собирает и сканирует пять images, разворачивает staging, выполняет
smoke/load/restore/resilience gates и останавливается на GitHub Environment approval перед
production. Annotated tag и GitHub Release `v1.0.0` создаются только после успешного production
smoke, поэтому tag всегда указывает на фактически развёрнутый commit.

Цели и бюджеты описаны в [docs/slo.md](docs/slo.md), полный порядок — в
[release checklist](docs/release-checklist.md) и [production runbooks](docs/runbooks/deployment.md).

## Материалы после занятия

Кнопка «Сформировать» по-прежнему доступна для ручного перезапуска. Основной автоматический путь:

1. BBB подписанным callback сообщает о готовности записи;
2. receipt, job и outbox event сохраняются одной транзакцией;
3. worker синхронизирует прямой медиаисточник и транскрибирует его;
4. транскрипт и заметки фиксируются как `LessonEvidenceBundle v1` с SHA-256;
5. генератор создаёт содержимое, `DocumentEngine` собирает TEX/HTML/PDF;
6. преподаватель проверяет комплект, согласует и отдельно публикует его.

Если webhook не указан, создаются два локальных черновика: итог занятия и домашнее задание.
Контракты описаны в [docs/materials-webhook.md](docs/materials-webhook.md) и
[docs/post-lesson-automation.md](docs/post-lesson-automation.md).
Версионирование, компиляция и review lifecycle описаны в
[docs/materials-factory.md](docs/materials-factory.md).
Кабинеты, модель доступа и безопасная доставка описаны в
[docs/portal-delivery.md](docs/portal-delivery.md).
Production PostgreSQL, параметры pool, backup и перенос SQLite описаны в
[docs/production-database.md](docs/production-database.md).
Очереди, lease, retry/dead-letter и действия оператора описаны в
[docs/durable-workers.md](docs/durable-workers.md).
Private S3/MinIO, антивирусная проверка, retention, восстановление и миграция локальных файлов
описаны в [docs/artifact-storage.md](docs/artifact-storage.md).

По умолчанию включён локальный детерминированный preview-движок. Для настоящей PDF-компиляции
подключите отдельный экземпляр `latex-for-everyone`:

```dotenv
DOCUMENT_ENGINE_PROVIDER=latex-for-everyone
DOCUMENT_ENGINE_URL=https://latex.example.com
DOCUMENT_ENGINE_TOKEN=service-access-token
ARTIFACT_STORAGE_PROVIDER=s3
ARTIFACT_S3_BUCKET=tutor-artifacts
# Для MinIO: ARTIFACT_S3_ENDPOINT_URL=https://minio.example.com
ARTIFACT_CLAMAV_ENABLED=true
```

Запись BigBlueButton появляется после серверной постобработки, иногда через несколько минут после
окончания встречи. Callback фиксируется сразу; отсутствие прямого audio/video URL переводит job в
управляемый retry.

Для локальной транскрибации:

```bash
uv sync --extra transcription
```

```dotenv
TRANSCRIPTION_PROVIDER=faster-whisper
TRANSCRIPTION_MODEL=small
TRANSCRIPTION_DEVICE=cpu
TRANSCRIPTION_COMPUTE_TYPE=int8
```

## Диагностика

```bash
uv run python -m pytest
uv run ruff check .
make security
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

`/health/ready` проверяет PostgreSQL, Redis, S3 и BBB, возвращает `503` при отказе обязательного
компонента и не раскрывает текст исключения. В локальном eager/demo-профиле необязательные
адаптеры помечаются как `eager`, `local` и `demo`.

`/metrics` закрыт Bearer-токеном, если задан `METRICS_BEARER_TOKEN`. Docker Compose передаёт его
Prometheus через secret-файл. Grafana доступна на <http://localhost:3000>, Prometheus — на
<http://localhost:9090>; трассы HTTP/Celery сохраняются в Tempo. Полная модель защиты, параметры
reverse proxy, правила редактирования логов и runbook alerts описаны в
[docs/security-observability.md](docs/security-observability.md).

## Структура

```text
src/tutor_assistant_web/
├── app.py                 # минимальная точка запуска
├── bootstrap/             # composition root, DI-контейнер, реестр модулей
├── modules/
│   ├── identity/          # доступ и сессии
│   ├── audit/             # журнал действий организации
│   ├── students/          # CRM учеников
│   ├── scheduling/        # расписание
│   ├── classroom/         # занятие и записи
│   ├── automation/        # callback, outbox, транскрипт и workflow
│   ├── materials/         # evidence и артефакты
│   ├── portal/            # кабинеты, доставки и уведомления
│   └── dashboard/         # главная страница и health checks
├── providers/             # BBB/demo, webhook/local, Celery/inline
├── shared/                # контракты, ошибки, security, web helpers
├── bbb.py                 # низкоуровневый checksum API
├── models.py              # compatibility exports
├── services.py            # compatibility facade
├── worker.py              # Celery entrypoint
├── static/                # минималистичный UI
└── templates/             # server-rendered страницы
```

Каждый модуль содержит собственные модели, application-сервисы и HTTP routes. Маршруты не
обращаются к SQLAlchemy и BigBlueButton напрямую. Composition root выбирает реализации контрактов
`ConferenceProvider`, `TranscriptionProvider`, `MaterialGenerator`, `DocumentEngine`,
`ArtifactStorage` и `JobDispatcher`.

## Пользователи, роли и организации

При первом запуске создаются организация и администратор из `BOOTSTRAP_*`. Пароль хранится как
Argon2-хеш. После входа каждый application-сервис получает `organization_id` из подписанной сессии;
идентификатор из формы или query string для выбора организации не принимается.

Роли `admin` и `tutor` работают с административным интерфейсом. Роли `student` и `parent`
получают личный кабинет материалов. Администратор
управляет участниками в разделе «Команда»: создаёт приглашения, меняет роли и отключает доступ.
Защита не позволяет отключить собственное членство или удалить последнего администратора.

Приглашение хранится в базе в виде SHA-256 хеша токена. Исходная ссылка показывается
администратору один раз, действует `INVITATION_TTL_HOURS` часов и может быть отозвана. Пользователь
с существующим email подтверждает приглашение своим текущим паролем.

Если пользователь состоит в нескольких организациях, селектор рабочего пространства появляется
в боковой панели. Сервер проверяет membership при каждом переключении и обновляет tenant-контекст
подписанной сессии.

Для явного обновления схемы:

```bash
uv run alembic upgrade head
```

`AUTO_MIGRATE=true` доступен для локальной разработки. Production требует
`AUTO_MIGRATE=false` и отдельный migration job. При обновлении с версии 0.2
существующие ученики, занятия, записи и материалы сохраняются и относятся к начальной организации.
Перед production-обновлением всё равно создайте backup базы.

Раздел «Аудит» доступен администраторам. Он фиксирует создание и принятие приглашений, изменение
membership, переключение workspace, создание и изменение учеников, создание и завершение занятий,
редактирование заметок и запуск генерации материалов.

Для ограниченного запуска перечислите корневые модули:

```dotenv
ENABLED_MODULES=students,scheduling
```

Зависимости подключаются автоматически. Пустое значение включает весь встроенный набор.

Архитектурные границы и модель доверия описаны в
[docs/architecture.md](docs/architecture.md).

## Ограничения пилота

- доставка приглашений по email пока не подключена: администратор передаёт ссылку вручную;
- нет повторяющихся событий и интеграции с внешними календарями;
- нет платежей и внешних email/Telegram-уведомлений;
- качество транскрипции зависит от выбранной Whisper-модели и качества записи;
- diarization говорящих пока не подключён;
- локальные AI-материалы являются шаблонными черновиками;
- demo-доска служит только для знакомства с интерфейсом и не синхронизируется;
- юридические основания обработки данных несовершеннолетних и тексты согласий задаются владельцем развёртывания.

Пилот предназначен для проверки реального сценария на нескольких занятиях. Перед работой с
персональными данными несовершеннолетних требуется определить согласия, сроки хранения, резервное
копирование и правила доступа.

## Ближайший production backlog

1. Email/Telegram-уведомления и настройки предпочтений получателя.
2. Diarization говорящих и словарь терминов конкретного ученика.
3. Повторяющееся расписание, уведомления и iCal.
4. Централизованный secrets manager и автоматическая ротация ключей.
5. Backup/restore и disaster recovery drill для PostgreSQL, S3 и Tempo.
